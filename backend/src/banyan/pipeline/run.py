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
    await mark_skip_or("freshness", fresh.skipped_because, "ok", needs_web=fresh.needs_web)

    await mark(
        "retrieve", found.status, retrieval_ids=found.retrieval_ids,
        entries=len(found.entries), memories=len(found.memories),
    )  # fmt: skip

    verdict = await steps.judge(inp, fresh, found, deps)
    await mark_skip_or(
        "judge", verdict.skipped_because, "hit" if verdict.hit else "miss",
        coverage=verdict.coverage, missing=verdict.missing,
    )  # fmt: skip

    await mark("answer", "running")
    answered = await steps.answer(inp, fresh, found, verdict, deps)
    deps.path[-1] = PathEntry(step="answer", status="ok", detail={"source": answered.source})
    for citation in answered.citations:
        await deps.emit(CitationEvent(citation))

    written = await steps.writeback(inp, answered, deps)
    await mark("writeback", "ok", jobs=[j.kind for j in written.jobs])

    return AskResult(
        answer=answered,
        retrieval_ids=found.retrieval_ids,
        jobs=written.jobs,
    )
