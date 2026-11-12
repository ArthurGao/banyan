"""Builds the worker from settings and runs it: job slots plus the stale-stream sweeper."""

import asyncio
import contextlib
import logging
import os
import socket
from functools import partial

from banyan.jobs import JobQueue, run_worker
from banyan.models import Job, JobKind
from banyan.worker.context import WorkerContext
from banyan.worker.handlers import handlers, settled
from banyan.worker.sweep import sweep_stale_streams

log = logging.getLogger("banyan.worker")

SWEEP_EVERY_S = 60.0


def queue_for(ctx: WorkerContext) -> JobQueue:
    return JobQueue(
        ctx.sessions,
        Job.__table__,  # type: ignore[arg-type]
        worker_id=f"{socket.gethostname()}:{os.getpid()}",
        lease_s=ctx.settings.job_lease_s,
        max_attempts=ctx.settings.job_max_attempts,
    )


async def run(ctx: WorkerContext, stop: asyncio.Event) -> None:
    queue = queue_for(ctx)
    registry = handlers(ctx)
    log.info("worker %s handles %s", queue.worker_id, sorted(registry))
    caps = {JobKind.NODE_EXPLAIN.value: ctx.settings.bulk_concurrency}
    sweeper = asyncio.create_task(_sweep_forever(ctx, stop))
    try:
        await run_worker(
            queue, registry, slots=ctx.settings.worker_slots, caps=caps, stop=stop,
            on_settled=partial(settled, ctx),
        )  # fmt: skip
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper


async def _sweep_forever(ctx: WorkerContext, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            swept = await sweep_stale_streams(
                ctx.sessions, older_than_s=ctx.settings.stream_stale_s
            )
            if swept:
                log.warning("marked %d interrupted answer(s) failed", swept)
        except Exception:
            log.exception("sweeping stale answers failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=SWEEP_EVERY_S)
