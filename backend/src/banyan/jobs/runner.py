"""The worker loop: N slots claim and run jobs; a running handler keeps its lease alive."""

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping

from banyan.jobs.queue import Claimed, JobQueue, RetryLater

log = logging.getLogger("banyan.jobs")

Handler = Callable[[Claimed], Awaitable[None]]
# Called after a job reached its next state (done, pending or dead): e.g. to settle what the job
# was part of.
Settled = Callable[[Claimed, str], Awaitable[None]]


async def run_worker(
    queue: JobQueue,
    handlers: Mapping[str, Handler],
    *,
    slots: int,
    caps: Mapping[str, int] | None = None,
    poll_s: float = 1.0,
    stop: asyncio.Event | None = None,
    on_settled: Settled | None = None,
) -> None:
    stop = stop or asyncio.Event()
    await asyncio.gather(
        *(_slot(queue, handlers, caps or {}, poll_s, stop, on_settled) for _ in range(slots))
    )


async def run_one(queue: JobQueue, handlers: Mapping[str, Handler], job: Claimed) -> str:
    """Runs a claimed job to its next state: done, pending, dead, or lost (the lease went to
    another claim, so this handler is cancelled and leaves the job to its new holder)."""
    work: asyncio.Future[None] = asyncio.ensure_future(handlers[job.kind](job))
    lease = asyncio.create_task(_keep_lease(queue, job))
    try:
        await asyncio.wait({work, lease}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        if not work.done():  # the lease was lost, or this worker is stopping
            work.cancel()
        lease.cancel()
        await asyncio.gather(work, lease, return_exceptions=True)
    if work.cancelled():
        return "lost"
    exc = work.exception()
    if isinstance(exc, RetryLater):
        await queue.retry_later(job, exc)
        return "pending"
    if exc is not None:
        status = await queue.fail(job, exc)
        log.warning("job %s (%s) failed, now %s: %s", job.id, job.kind, status, exc)
        return status
    await queue.complete(job)
    return "done"


async def _slot(
    queue: JobQueue,
    handlers: Mapping[str, Handler],
    caps: Mapping[str, int],
    poll_s: float,
    stop: asyncio.Event,
    on_settled: Settled | None,
) -> None:
    while not stop.is_set():
        try:
            job = await queue.claim(handlers.keys(), caps)
        except Exception:
            log.exception("claiming a job failed")
            job = None
        if job is None:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=poll_s)
            continue
        status = await run_one(queue, handlers, job)
        if on_settled is not None:
            try:
                await on_settled(job, status)
            except Exception:
                log.exception("settling job %s failed", job.id)


async def _keep_lease(queue: JobQueue, job: Claimed) -> None:
    interval = max(queue.lease.total_seconds() / 3, 0.05)
    while True:
        await asyncio.sleep(interval)
        if not await queue.heartbeat(job):
            log.warning("job %s lost its lease to another worker", job.id)
            return
