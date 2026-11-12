"""The steps of the ask pipeline (design §6.2). Each is `async def step(inp, deps) -> out`; the
routing decisions are code, not a model (AGENTS.md rule 3). Each step records why it did or did
not run.
"""

import asyncio
from collections.abc import Callable, Sequence
from datetime import timedelta

from banyan.llm import CallContext, Role, Tool, ToolCalled
from banyan.models import Source
from banyan.pipeline.events import TokenEvent, ToolEvent
from banyan.pipeline.sources import Wrapper
from banyan.pipeline.tools import SourceBook, ToolLog, build_tools, drop_expired
from banyan.pipeline.types import (
    CHARS_PER_TOKEN,
    AnswerOut,
    AskInput,
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


def _context(inp: AskInput) -> CallContext:
    return CallContext(
        topic_id=inp.topic_id,
        message_id=inp.message_id,
        path_kind=inp.mode,
        generation_run_id=inp.generation_run_id,
    )


async def scope(inp: AskInput, deps: Deps) -> ScopeOut:
    """Nearest first: this node, this topic, every active topic (design §6.2 step 1)."""
    topic = deps.config.with_inbox([inp.collection])  # §8.5
    every = deps.config.with_inbox(inp.all_collections)
    scopes = [
        Scope(collections=[inp.collection], tags=[f"node:{inp.node_id}"]),
        Scope(collections=topic),
    ]
    if set(every) != set(topic):  # with one topic, "all" is "this topic"
        scopes.append(Scope(collections=every))
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
                top_k=deps.config.top_k,
                token_budget=deps.config.context_budget,
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
            if used + cost > deps.config.context_budget:
                continue
            source = RetrievedSource.from_item(item, result.retrieval_id)
            if item.kind == "memory":
                out.memories.append(source)
            elif len(out.entries) < deps.config.top_k:
                out.entries.append(source)
            else:
                continue
            used += cost
    if out.status == "unavailable":
        out.entries = []  # knowledge from a degraded KB is not used (design §6.2 step 3)
    else:
        out.entries = await drop_expired(deps, out.entries)
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
    wrapper = Wrapper()
    prompt = render(
        "judge_sufficiency", question=inp.question, mode=inp.mode,
        node_summary=inp.node_summary, sources=SourceBook(found).initial(wrapper),
        sources_rule=wrapper.rule,
    )  # fmt: skip
    verdict = await deps.llm.structured(
        Role.JUDGE_SUFFICIENCY, SufficiencyVerdict, instructions=prompt.instructions,
        prompt=prompt.prompt, context=_context(inp), usage=deps.usage,
    )  # fmt: skip
    threshold = (
        deps.config.judge_threshold_explain
        if inp.mode == "explain"
        else deps.config.judge_threshold
    )
    hit = verdict.sufficient and verdict.coverage >= threshold
    return JudgeOut(hit=hit, coverage=verdict.coverage, missing=verdict.missing)


def web_on(inp: AskInput, deps: Deps) -> bool:
    """Web tools are offered when a search provider is configured and the request allows it."""
    return deps.web is not None and inp.allow_web


async def answer(
    inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, verdict: JudgeOut, deps: Deps
) -> AnswerOut:
    """A hit: `answer.from_kb`, only from the knowledge, citing it. Otherwise the answer loop:
    `answer.compose`, or `node.explain` for an explanation, starting from the retrieval, the
    topic's research and what the judge found missing, with kb_search and (web on) web_search /
    web_fetch (design §5.2, §6.2 step 5)."""
    if verdict.hit:
        return await _from_kb(inp, fresh, found, deps)
    role = Role.NODE_EXPLAIN if inp.mode == "explain" else Role.ANSWER_COMPOSE
    web = web_on(inp, deps)
    book, wrapper = SourceBook(found, inp.research), Wrapper()
    log: ToolLog = []
    tools = build_tools(inp, deps, book, wrapper, log, web=web)
    material = book.initial(wrapper)
    template = "node_explain" if inp.mode == "explain" else "answer_compose"
    prompt = render(
        template, **_context_vars(inp), sources=material, missing=verdict.missing,
        sources_rule=wrapper.rule if material or tools else "",
        kb_tool=deps.steward is not None, web=web, search_first=web and fresh.needs_web,
    )  # fmt: skip
    content = await _stream(
        role, prompt.instructions, prompt.prompt, inp, deps, tools=tools,
        max_output_tokens=deps.config.answer_max_tokens if role == Role.ANSWER_COMPOSE else None,
    )  # fmt: skip
    # Without the web, a time-sensitive answer comes from what the model knows (row 4b).
    warnings = ["stale_risk"] if fresh.needs_web and not web else []
    citations = book.citations(content)
    read = [c.fetched_at for c in citations if c.fetched_at is not None]
    return AnswerOut(
        content=content, source=book.source(content), warnings=warnings, citations=citations,
        retrieval_ids=book.retrieval_ids, tools=log,
        roles=_roles(fresh, role, deps, judged=verdict.skipped_because is None),
        # What the web said holds until the earliest page it cites gets old (design §8.2).
        valid_to=min(read) + timedelta(days=deps.config.web_valid_days) if read else None,
    )  # fmt: skip


async def _from_kb(inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, deps: Deps) -> AnswerOut:
    book, wrapper = SourceBook(found), Wrapper()
    prompt = render(
        "answer_from_kb", **_context_vars(inp), sources=book.initial(wrapper),
        sources_rule=wrapper.rule,
    )  # fmt: skip

    def must_cite(text: str) -> None:
        # An answer from the knowledge base must say which knowledge (owner decision, design §6.2);
        # the role layer records this as a failed (validation) attempt.
        if not book.cited(text):
            raise ValueError("the answer cites none of the sources")

    content = await _stream(
        Role.ANSWER_FROM_KB, prompt.instructions, prompt.prompt, inp, deps, validate=must_cite
    )
    return AnswerOut(
        content=content, source=Source.KB, citations=book.citations(content),
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
    for entry_id in sorted(deps.expired_seen):  # expired web knowledge leaves the KB (§8.2)
        jobs.append(
            JobSpec(
                kind="kb_deprecate",
                idempotency_key=f"kb_deprecate:expired:{entry_id}",
                payload={"entry_ids": [entry_id]},
            )
        )
    if inp.episode_due:
        # The caller counted the branch's new turns (design §8.5); the job checks again.
        jobs.append(
            JobSpec(
                kind="episode_submit",
                idempotency_key=f"episode_submit:{inp.message_id}",
                payload={"branch_id": str(inp.branch_id)},
                dedupe_key=f"episode:{inp.branch_id}",
            )
        )
    if inp.compress_due:
        # Turns the summary does not cover no longer fit: compress them for the next question.
        jobs.append(
            JobSpec(
                kind="branch_compress",
                idempotency_key=f"branch_compress:{inp.message_id}",
                payload={"branch_id": str(inp.branch_id)},
                dedupe_key=f"compress:{inp.branch_id}",
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


def _context_vars(inp: AskInput) -> dict[str, object]:
    return {
        "topic_title": inp.topic_title, "goal": inp.goal, "level": inp.level,
        "language_name": inp.language_name, "instructions": inp.instructions,
        "outline": inp.outline, "node_title": inp.node_title, "node_summary": inp.node_summary,
        "explanation": inp.explanation, "history": [t.model_dump() for t in inp.history],
        "history_summary": inp.history_summary,
        "question": inp.question, "mode": inp.mode,
    }  # fmt: skip


async def _stream(
    role: Role, instructions: str, prompt: str, inp: AskInput, deps: Deps,
    validate: Callable[[str], None] | None = None, tools: Sequence[Tool] = (),
    max_output_tokens: int | None = None,
) -> str:  # fmt: skip
    """Streams the answer as tokens and each tool call as a `tool` event. The answer is the text
    after the last tool call; what came before was the model thinking aloud (design §9)."""
    parts: list[str] = []
    async for item in deps.llm.stream(
        role, instructions=instructions, prompt=prompt, context=_context(inp), usage=deps.usage,
        validate=validate, tools=tools, max_output_tokens=max_output_tokens,
        max_steps=deps.config.answer_max_steps if tools else None,
        max_total_tokens=deps.config.answer_loop_tokens if tools else None,
        timeout_s=deps.config.answer_timeout_s if tools else None,
    ):  # fmt: skip
        if isinstance(item, ToolCalled):
            parts = []
            await deps.emit(ToolEvent(item.name, _arg(item, "query"), _arg(item, "url")))
        else:
            parts.append(item)
            await deps.emit(TokenEvent(item))
    return "".join(parts)


def _arg(call: ToolCalled, name: str) -> str | None:
    value = call.args.get(name)
    return value if isinstance(value, str) else None


def _roles(fresh: FreshnessOut, answered_by: Role, deps: Deps, *, judged: bool) -> list[str]:
    """Provenance: every role that took part, in order (AGENTS.md rule 5)."""
    roles = [Role.JUDGE_FRESHNESS] if fresh.ran else []
    if judged:
        roles.append(Role.JUDGE_SUFFICIENCY)
    return [deps.llm.provenance(r) for r in [*roles, answered_by]]
