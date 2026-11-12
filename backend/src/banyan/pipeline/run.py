"""The orchestrator (design §6.0): chains the steps and branches on their outputs; every I/O is in a
step. Requests and workers call the same function; only `deps.emit` differs."""

import asyncio

from banyan.pipeline import steps
from banyan.pipeline.events import CitationEvent, PathEntry, PathEvent
from banyan.pipeline.types import AskInput, AskResult, Deps


async def run_ask(inp: AskInput, deps: Deps) -> AskResult:
    async def mark(step: str, status: str, **detail: object) -> None:
        entry = PathEntry(step=step, status=status, detail=detail)
        deps.path.append(entry)
        await deps.emit(PathEvent(entry))

    async def mark_skip_or(
        step: str, skipped_because: str | None, status: str, **detail: object
    ) -> None:
        if skipped_because:
            await mark(step, "skipped", reason=skipped_because)
        else:
            await mark(step, status, **detail)

    scopes = await steps.scope(inp, deps)
    # The first path event carries the placeholder's id (design §9).
    await mark("scope", "ok", message_id=str(inp.message_id), scopes=len(scopes.scopes))

    # Independent, so concurrent; their path entries still come in a fixed order.
    fresh, found = await asyncio.gather(
        steps.freshness(inp, deps), steps.retrieve(inp, scopes, deps)
    )
    freshness: dict[str, object] = {"needs_web": fresh.needs_web}
    if fresh.needs_web and not steps.web_on(inp, deps):  # answered without the web (row 4b)
        freshness["warning"] = "needs_web_but_web_disabled"
    await mark_skip_or("freshness", fresh.skipped_because, "ok", **freshness)

    await mark(
        "retrieve", found.status, retrieval_ids=found.retrieval_ids,
        entries=len(found.entries), memories=len(found.memories), expired=len(deps.expired_seen),
    )  # fmt: skip

    verdict = await steps.judge(inp, fresh, found, deps)
    await mark_skip_or(
        "judge", verdict.skipped_because, "hit" if verdict.hit else "miss",
        coverage=verdict.coverage, missing=verdict.missing,
    )  # fmt: skip

    await mark("answer", "running")
    answered = await steps.answer(inp, fresh, found, verdict, deps)
    detail: dict[str, object] = {"source": answered.source}
    if answered.tools:
        detail["tools"] = answered.tools
    deps.path[-1] = PathEntry(step="answer", status="ok", detail=detail)
    for citation in answered.citations:
        await deps.emit(CitationEvent(citation))

    written = await steps.writeback(inp, answered, deps)
    await mark("writeback", "ok", jobs=[j.kind for j in written.jobs])

    return AskResult(
        answer=answered,
        retrieval_ids=[*found.retrieval_ids, *answered.retrieval_ids],
        jobs=written.jobs,
    )
