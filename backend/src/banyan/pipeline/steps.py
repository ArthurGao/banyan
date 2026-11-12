"""The steps of the ask pipeline (design §6.2). Each is `async def step(inp, deps) -> out`; the
routing decisions are code, not a model (AGENTS.md rule 3).

B1 is the skeleton: retrieval runs against whatever steward is wired, `judge.sufficiency` and
`answer.from_kb` arrive in B3, web tools in B4. Each step records why it did or did not run.
"""

from banyan.llm import CallContext, Role
from banyan.models import Source
from banyan.pipeline.events import TokenEvent
from banyan.pipeline.sources import SourceText, wrap
from banyan.pipeline.types import (
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
    return ScopeOut(
        scopes=[
            Scope(collections=[inp.collection], tags=[f"node:{inp.node_id}"]),
            Scope(collections=[inp.collection]),
            Scope(collections=inp.all_collections),
        ]
    )


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
    """Always runs (design §6.2 step 3). Steward failing, or answering without its KB, is the one
    degradation: the answer goes on without knowledge (row 8)."""
    if deps.steward is None:
        return RetrieveOut(status="not_configured")
    out = RetrieveOut(status="ok")
    seen: set[str] = set()
    for sc in scopes.scopes:
        try:
            result = await deps.steward.retrieve(
                inp.question,
                user_id=deps.user_id,
                collections=sc.collections,
                tags=sc.tags or None,
                top_k=deps.top_k,
                token_budget=deps.context_budget,
            )
        except StewardUnavailable:
            break
        out.retrieval_ids.append(result.retrieval_id)
        if "kb" in result.degraded:
            break
        for item in result.items:
            if item.id not in seen:
                seen.add(item.id)
                source = RetrievedSource(id=item.id, title=item.title, content=item.content)
                (out.memories if item.kind == "memory" else out.entries).append(source)
        if len(out.entries) >= deps.top_k:
            return out
    else:
        return out
    # Unavailable: knowledge already gathered is dropped too, memories stay (design §6.2 step 3).
    return out.model_copy(update={"status": "unavailable", "entries": []})


async def judge(inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, deps: Deps) -> JudgeOut:
    if inp.force_llm:
        return JudgeOut(hit=False, skipped_because="force_llm")
    if fresh.needs_web:
        return JudgeOut(hit=False, skipped_because="needs_web")
    if found.status != "ok":
        return JudgeOut(hit=False, skipped_because=f"retrieve_{found.status}")
    if not found.entries:
        return JudgeOut(hit=False, skipped_because="no_entries")
    # `judge.sufficiency` and the KB-only answer arrive in B3 (design §12).
    return JudgeOut(hit=False, skipped_because="not_built_yet")


async def answer(inp: AskInput, fresh: FreshnessOut, found: RetrieveOut, deps: Deps) -> AnswerOut:
    """`answer.compose`, or `node.explain` for an explanation, streamed (design §5.2, §6.2)."""
    role = Role.NODE_EXPLAIN if inp.mode == "explain" else Role.ANSWER_COMPOSE
    material = [SourceText(s.id, "kb", _source_text(s)) for s in found.entries]
    material += [SourceText(s.id, "memory", s.content) for s in found.memories]
    wrapped = wrap(material)
    template = "node_explain" if inp.mode == "explain" else "answer_compose"
    prompt = render(
        template,
        topic_title=inp.topic_title,
        goal=inp.goal,
        level=inp.level,
        language_name=inp.language_name,
        instructions=inp.instructions,
        outline=inp.outline,
        node_title=inp.node_title,
        node_summary=inp.node_summary,
        explanation=inp.explanation,
        history=[t.model_dump() for t in inp.history],
        question=inp.question,
        sources=wrapped.text,
        sources_rule=wrapped.rule if material else "",
    )
    parts: list[str] = []
    async for delta in deps.llm.stream(
        role,
        instructions=prompt.instructions,
        prompt=prompt.prompt,
        context=_context(inp),
        usage=deps.usage,
    ):
        parts.append(delta)
        await deps.emit(TokenEvent(delta))
    roles = [deps.llm.provenance(role)]
    if fresh.ran:
        roles.insert(0, deps.llm.provenance(Role.JUDGE_FRESHNESS))
    # No web tools before B4, so a time-sensitive question is answered from what the model knows.
    warnings = ["stale_risk"] if fresh.needs_web else []
    return AnswerOut(content="".join(parts), source=Source.LLM, roles=roles, warnings=warnings)


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
