"""Job handlers by kind. A kind without a handler here is not claimed and waits for its stage
(kb_propose, kb_feedback, kb_deprecate: B3; episode_submit: B5)."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial

from banyan.errors import NotFound
from banyan.jobs import Claimed, Handler, Permanent, RetryLater
from banyan.models import JobKind, Topic
from banyan.pipeline.events import ErrorEvent, Event
from banyan.services import answers, notes, proposals, runs
from banyan.steward import StewardRejected, collection_name
from banyan.worker.context import WorkerContext

# Model-written entries go live without a person's look (owner decision, design §8.1).
REVIEW_POLICY = {"low": "auto", "medium": "auto"}
BUSY_RETRY_S = 15.0
WAITING_RUN_RETRY_S = 10.0
VERSION_RETRY_S = 1.0
COLLECTION_RETRY_S = 30.0


def handlers(ctx: WorkerContext) -> dict[str, Handler]:
    registry: dict[str, Handler] = {
        JobKind.NODE_EXPLAIN: partial(node_explain, ctx),
        JobKind.NOTES_UPDATE: partial(notes_update, ctx),
    }
    if ctx.steward is not None:  # the KB writes wait for steward (design §8)
        registry[JobKind.COLLECTION_ENSURE] = partial(collection_ensure, ctx)
        registry[JobKind.KB_PROPOSE] = partial(kb_propose, ctx)
        registry[JobKind.KB_FEEDBACK] = partial(kb_feedback, ctx)
        registry[JobKind.KB_DEPRECATE] = partial(kb_deprecate, ctx)
    return registry


async def settled(ctx: WorkerContext, job: Claimed, status: str) -> None:
    """A run's status settles when one of its jobs ends, done or dead (design §5.1.1)."""
    if job.kind == JobKind.NODE_EXPLAIN and "run_id" in job.payload and status in ("done", "dead"):
        async with ctx.sessions() as s:
            await runs.settle(s, uuid.UUID(job.payload["run_id"]))


async def collection_ensure(ctx: WorkerContext, job: Claimed) -> None:
    """The topic's steward collection (design §5.1 step 6); a no-op once it is known."""
    assert ctx.steward is not None
    topic_id = uuid.UUID(job.payload["topic_id"])
    async with ctx.sessions() as s:
        topic = await s.get(Topic, topic_id)
        if topic is None or topic.steward_collection_id:
            return  # deleted meanwhile, or done before
    with _steward_rejections_final():
        collection_id = await ctx.steward.ensure_collection(
            collection_name(topic_id), review_policy=REVIEW_POLICY,
            idempotency_key=job.idempotency_key,
        )  # fmt: skip
    async with ctx.sessions() as s:
        topic = await s.get(Topic, topic_id)
        if topic is not None:
            topic.steward_collection_id = collection_id
            await s.commit()


async def node_explain(ctx: WorkerContext, job: Claimed) -> None:
    """One node's explanation in the background (design §5.1.1): the same pipeline as a request,
    tokens dropped. Explained already → nothing to do; another answer streaming on it → later
    (a dead process's leftover is failed by the worker's sweeper)."""
    node_id = uuid.UUID(job.payload["node_id"])
    run_id = uuid.UUID(job.payload["run_id"]) if "run_id" in job.payload else None
    if run_id is not None:
        async with ctx.sessions() as s:
            admission = await runs.admit(s, run_id)
        if admission == "over":
            return
        if admission == "wait":  # paused or out of budget: parked until it runs again
            raise RetryLater("the run is not running", after_s=WAITING_RUN_RETRY_S)
    async with ctx.sessions() as s:
        try:
            inp = await answers.start_explain(s, node_id)
        except (answers.AlreadyExplained, NotFound):
            return
        except answers.AnswerStreaming as exc:
            raise RetryLater("the node is being explained", after_s=BUSY_RETRY_S) from exc
    inp = inp.model_copy(update={"generation_run_id": run_id})
    last: Event | None = None
    rt = answers.Runtime.from_settings(ctx.llm, ctx.steward, ctx.sessions, ctx.settings)
    async for event in answers.stream_answer(inp, rt):
        last = event
    if isinstance(last, ErrorEvent):
        raise RuntimeError(f"{last.code}: {last.message}")  # retried with backoff


async def notes_update(ctx: WorkerContext, job: Claimed) -> None:
    """Merges what the node's notes do not include yet (design §5.4); a lost version race runs
    again on the new version."""
    try:
        await notes.update_notes(ctx.sessions, ctx.llm, uuid.UUID(job.payload["node_id"]))
    except NotFound:
        return  # the node was deleted
    except notes.VersionConflict as exc:
        raise RetryLater(str(exc), after_s=VERSION_RETRY_S) from exc


async def kb_propose(ctx: WorkerContext, job: Claimed) -> None:
    """An answer the strong model wrote goes back to the KB (design §6.2 step 6)."""
    assert ctx.steward is not None
    try:
        with _steward_rejections_final():
            await proposals.propose(
                ctx.sessions, ctx.llm, ctx.steward, uuid.UUID(job.payload["message_id"]),
                idempotency_key=job.idempotency_key,
            )  # fmt: skip
    except NotFound:
        return
    except proposals.CollectionNotReady as exc:
        raise RetryLater(str(exc), after_s=COLLECTION_RETRY_S) from exc


async def kb_feedback(ctx: WorkerContext, job: Claimed) -> None:
    assert ctx.steward is not None
    with _steward_rejections_final():
        await proposals.give_feedback(
            ctx.sessions, ctx.steward, uuid.UUID(job.payload["message_id"]),
            helpful=bool(job.payload["helpful"]), note=job.payload.get("note"),
            idempotency_key=job.idempotency_key,
        )  # fmt: skip


async def kb_deprecate(ctx: WorkerContext, job: Claimed) -> None:
    """A wrong answer's proposed entries, or the given ones (a deleted node's)."""
    assert ctx.steward is not None
    with _steward_rejections_final():
        if "entry_ids" in job.payload:
            await proposals.deprecate(
                ctx.steward, job.payload["entry_ids"], idempotency_key=job.idempotency_key
            )
        else:
            await proposals.deprecate_proposed(
                ctx.sessions, ctx.steward, uuid.UUID(job.payload["message_id"]),
                idempotency_key=job.idempotency_key,
            )  # fmt: skip


@contextmanager
def _steward_rejections_final() -> Iterator[None]:
    """A steward 4xx is replayed for 24 h under the same key (design §4): retrying is useless."""
    try:
        yield
    except StewardRejected as exc:
        raise Permanent(str(exc)) from exc
