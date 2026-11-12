"""The steps of the ask pipeline (design §6.2). Each is `async def step(inp, deps) -> out`; the
routing decisions are code, not a model (AGENTS.md rule 3). Each step records why it did or did
not run. Web tools arrive in B4.
"""

import asyncio
import re
from collections.abc import Callable

from banyan.llm import CallContext, Role
from banyan.models import Source
from banyan.pipeline.events import TokenEvent
from banyan.pipeline.sources import SourceText, Wrapped, wrap
from banyan.pipeline.types import (
    CHARS_PER_TOKEN,
    AnswerOut,
    AskInput,
    Citation,
    Deps,
    FreshnessOut,
    FreshnessVerdict,
    JobSpec,
    JudgeOut,
    RetrievedSource,
    RetrieveOut,
    Scope,
    ScopeOut,
    SufficiencyVerdict,
    WritebackOut,
)
from banyan.prompts import render
from banyan.steward import StewardUnavailable

CITATION = re.compile(r"\[(S\d+)\]")


def _context(inp: AskInput) -> CallContext:
    return CallContext(
        topic_id=inp.topic_id,
        message_id=inp.message_id,
        path_kind=inp.mode,
        generation_run_id=inp.generation_run_id,
    )


async def scope(inp: AskInput, deps: Deps) -> ScopeOut:
    """Nearest first: this node, this topic, every active topic (design §6.2 step 1)."""
    scopes = [
        Scope(collections=[inp.collection], tags=[f"node:{inp.node_id}"]),
        Scope(collections=[inp.collection]),
    ]
    if set(inp.all_collections) != {inp.collection}:  # with one topic, "all" is "this topic"
        scopes.append(Scope(collections=inp.all_collections))
    return ScopeOut(scopes=scopes)


async def freshness(inp: AskInput, deps: Deps) -> FreshnessOut:
    if inp.force_llm:
        return FreshnessOut(skipped_because="force_llm")
    if not deps.llm.configured(Role.JUDGE_FRESHNESS):
        return FreshnessOut(skipped_because="role_not_configured")
    prompt = render("judge_freshness", question=inp.question)
    verdict = await deps.llm.structured(
        Role.JUDGE_FRESHNESS,
        FreshnessVerdict,
        instructions=prompt.instructions,
        prompt=prompt.prompt,
        context=_context(inp),
        usage=deps.usage,
    )
    return FreshnessOut(needs_web=verdict.needs_web)


async def retrieve(inp: AskInput, scopes: ScopeOut, deps: Deps) -> RetrieveOut:
    """Always runs (design §6.2 step 3). The scopes are asked at once and merged nearest first.
    Steward failing, or answering without its KB, is the one degradation: the answer goes on
    without knowledge, keeping the memories it got (row 8)."""
    if deps.steward is None:
        return RetrieveOut(status="not_configured")
    steward = deps.steward
    results = await asyncio.gather(
        *(
            steward.retrieve(
                inp.question,
                user_id=deps.user_id,
                collections=sc.collections,
                tags=sc.tags or None,
                top_k=deps.top_k,
                token_budget=deps.context_budget,
            )
            for sc in scopes.scopes
        ),
        return_exceptions=True,
    )
    out = RetrieveOut(status="ok")
    seen: set[str] = set()
    # Each scope is given the whole budget, so the merge keeps the total inside it.
    used = 0
    for result in results:
        if isinstance(result, StewardUnavailable):
            out.status = "unavailable"
            continue
        if isinstance(result, BaseException):
            raise result
        out.retrieval_ids.append(result.retrieval_id)
        if "kb" in result.degraded:
            out.status = "unavailable"
        for item in result.items:
            if item.id in seen:
                continue
            seen.add(item.id)
            cost = len(item.content) // CHARS_PER_TOKEN + 1
            if used + cost > deps.context_budget:
                continue
            source = RetrievedSource(
                kind=item.kind, id=item.id, retrieval_id=result.retrieval_id, title=item.title,
                content=item.content,
            )  # fmt: skip
            if item.kind == "memory":
                out.memories.append(source)
            elif len(out.entries) < deps.top_k:
                out.entries.append(source)
            else:
                continue
            used += cost
    if out.status == "unavailable":
        out.entries = []  # knowledge from a degraded KB is not used (design §6.2 step 3)
    return out


async def judge(inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, deps: Deps) -> JudgeOut:
    """`judge.sufficiency` (design §6.2 step 4): a hit is decided by code from its verdict."""
    if inp.force_llm:
        return JudgeOut(hit=False, skipped_because="force_llm")
    if fresh.needs_web:
        return JudgeOut(hit=False, skipped_because="needs_web")
    if found.status != "ok":
        return JudgeOut(hit=False, skipped_because=f"retrieve_{found.status}")
    if not found.entries:
        return JudgeOut(hit=False, skipped_because="no_entries")
    wrapped, _ = _labelled(found)
    prompt = render(
        "judge_sufficiency", question=inp.question, mode=inp.mode,
        node_summary=inp.node_summary, sources=wrapped.text, sources_rule=wrapped.rule,
    )  # fmt: skip
    verdict = await deps.llm.structured(
        Role.JUDGE_SUFFICIENCY, SufficiencyVerdict, instructions=prompt.instructions,
        prompt=prompt.prompt, context=_context(inp), usage=deps.usage,
    )  # fmt: skip
    threshold = deps.judge_threshold_explain if inp.mode == "explain" else deps.judge_threshold
    hit = verdict.sufficient and verdict.coverage >= threshold
    return JudgeOut(hit=hit, coverage=verdict.coverage, missing=verdict.missing)


async def answer(
    inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, verdict: JudgeOut, deps: Deps
) -> AnswerOut:
    """A hit: `answer.from_kb`, only from the knowledge, citing it. Otherwise `answer.compose`, or
    `node.explain` for an explanation, with the retrieval and what the judge found missing as
    context (design §5.2, §6.2 step 5)."""
    if verdict.hit:
        return await _from_kb(inp, fresh, found, deps)
    role = Role.NODE_EXPLAIN if inp.mode == "explain" else Role.ANSWER_COMPOSE
    wrapped, _ = _labelled(found)
    template = "node_explain" if inp.mode == "explain" else "answer_compose"
    prompt = render(
        template, **_context_vars(inp), sources=wrapped.text, missing=verdict.missing,
        sources_rule=wrapped.rule if found.entries or found.memories else "",
    )  # fmt: skip
    content = await _stream(role, prompt.instructions, prompt.prompt, inp, deps)
    # No web tools before B4, so a time-sensitive question is answered from what the model knows.
    warnings = ["stale_risk"] if fresh.needs_web else []
    judged = verdict.skipped_because is None
    return AnswerOut(
        content=content, source=Source.LLM, warnings=warnings,
        roles=_roles(fresh, role, deps, judged=judged),
    )  # fmt: skip


async def _from_kb(inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, deps: Deps) -> AnswerOut:
    wrapped, labels = _labelled(found)
    prompt = render(
        "answer_from_kb", **_context_vars(inp), sources=wrapped.text, sources_rule=wrapped.rule
    )

    def cited(text: str) -> list[RetrievedSource]:
        return [labels[m] for m in dict.fromkeys(CITATION.findall(text)) if m in labels]

    def must_cite(text: str) -> None:
        # An answer from the knowledge base must say which knowledge (owner decision, design §6.2);
        # the role layer records this as a failed (validation) attempt.
        if not cited(text):
            raise ValueError("the answer cites none of the sources")

    content = await _stream(
        Role.ANSWER_FROM_KB, prompt.instructions, prompt.prompt, inp, deps, validate=must_cite
    )
    citations = [
        Citation(
            kind="kb",
            entry_id=s.id if s.kind == "entry" else None,
            item_id=s.id,
            retrieval_id=s.retrieval_id,
            title=s.title,
        )
        for s in cited(content)
    ]
    return AnswerOut(
        content=content, source=Source.KB, citations=citations,
        roles=_roles(fresh, Role.ANSWER_FROM_KB, deps, judged=True),
    )  # fmt: skip


async def writeback(inp: AskInput, answered: AnswerOut, deps: Deps) -> WritebackOut:
    """Jobs enqueued in the answer's transaction (design §6.2 step 6, AGENTS.md rule 6)."""
    jobs: list[JobSpec] = []
    if answered.source != Source.KB:
        jobs.append(
            JobSpec(
                kind="kb_propose",
                idempotency_key=f"kb_propose:{inp.message_id}",
                payload={"message_id": str(inp.message_id)},
            )
        )
    if inp.mode == "explain":
        jobs.append(
            JobSpec(
                kind="notes_update",
                idempotency_key=f"notes_update:{inp.message_id}",
                payload={"node_id": str(inp.node_id), "message_id": str(inp.message_id)},
                dedupe_key=f"notes:{inp.node_id}",
            )
        )
    return WritebackOut(jobs=jobs)


def _source_text(s: RetrievedSource) -> str:
    return f"{s.title}\n{s.content}" if s.title else s.content


def _labelled(found: RetrieveOut) -> tuple[Wrapped, dict[str, RetrievedSource]]:
    """Entries as S1…Sn (what an answer cites as [S1]), memories as M1…Mn (context only)."""
    labels = {f"S{i}": s for i, s in enumerate(found.entries, 1)}
    material = [SourceText(label, "kb", _source_text(s)) for label, s in labels.items()]
    material += [SourceText(f"M{i}", "memory", s.content) for i, s in enumerate(found.memories, 1)]
    return wrap(material), labels


def _context_vars(inp: AskInput) -> dict[str, object]:
    return {
        "topic_title": inp.topic_title, "goal": inp.goal, "level": inp.level,
        "language_name": inp.language_name, "instructions": inp.instructions,
        "outline": inp.outline, "node_title": inp.node_title, "node_summary": inp.node_summary,
        "explanation": inp.explanation, "history": [t.model_dump() for t in inp.history],
        "question": inp.question, "mode": inp.mode,
    }  # fmt: skip


async def _stream(
    role: Role, instructions: str, prompt: str, inp: AskInput, deps: Deps,
    validate: Callable[[str], None] | None = None,
) -> str:  # fmt: skip
    parts: list[str] = []
    async for delta in deps.llm.stream(
        role, instructions=instructions, prompt=prompt, context=_context(inp), usage=deps.usage,
        validate=validate,
    ):  # fmt: skip
        parts.append(delta)
        await deps.emit(TokenEvent(delta))
    return "".join(parts)


def _roles(fresh: FreshnessOut, answered_by: Role, deps: Deps, *, judged: bool) -> list[str]:
    """Provenance: every role that took part, in order (AGENTS.md rule 5)."""
    roles = [Role.JUDGE_FRESHNESS] if fresh.ran else []
    if judged:
        roles.append(Role.JUDGE_SUFFICIENCY)
    return [deps.llm.provenance(r) for r in [*roles, answered_by]]
