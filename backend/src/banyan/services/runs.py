"""Explanations made ahead of the learner (design §5.1, §5.1.1): `prefetch` (the next node) and
"generate all" runs with an estimate, a token budget, pause / resume / cancel and node retries.
Both enqueue `node_explain` jobs; the worker runs them through the same pipeline as a request.

A run's jobs are parked (`run_after = infinity`) while it is paused or out of budget and woken when
it runs again, so a waiting run costs nothing. Its status settles when a job ends (done or dead),
in the worker; reading progress never writes.
"""

import dataclasses
import statistics
import uuid
from typing import Literal

from sqlalchemy import Update, func, select, text, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import Conflict, NotFound, Unprocessable
from banyan.ids import uuid7
from banyan.models import (
    ACTIVE_RUN,
    Branch,
    ExplainMode,
    GenerationRun,
    Job,
    JobKind,
    JobStatus,
    Message,
    MessageStatus,
    NodeStatus,
    OutlineStatus,
    RunStatus,
    UsageLog,
    enqueue,
)
from banyan.services import branches, outline, topics
from banyan.services.lookup import get_or_404

HISTORY = 50  # explanations the per-node estimate is the median of
TOTAL_TOKENS = func.coalesce(UsageLog.input_tokens, 0) + func.coalesce(UsageLog.output_tokens, 0)
ExplanationStatus = Literal["none", "generating", "done", "failed"]
Admission = Literal["go", "wait", "over"]


@dataclasses.dataclass(frozen=True)
class Estimate:
    nodes: int
    tokens_per_node: int
    total: int
    basis: Literal["history", "default"]


async def enqueue_explain(
    s: AsyncSession, node_id: uuid.UUID, run_id: uuid.UUID | None = None
) -> None:
    """A `node_explain` job, unless one is already waiting for this node (dedupe, design §4)."""
    payload = {"node_id": str(node_id)} | ({"run_id": str(run_id)} if run_id else {})
    dedupe_key = f"explain:{node_id}"
    await enqueue(
        s, JobKind.NODE_EXPLAIN, key=f"node_explain:{node_id}:{uuid7()}", payload=payload,
        dedupe_key=dedupe_key,
    )  # fmt: skip
    if run_id is not None:
        # A prefetch job already waiting for the node absorbed this one (dedupe): it now works
        # for the run, so the run admits, counts and settles it.
        await s.execute(
            update(Job)
            .where(
                Job.dedupe_key == dedupe_key,
                Job.status == JobStatus.PENDING,
                ~Job.payload.has_key("run_id"),
            )
            .values(payload=Job.payload.concat({"run_id": str(run_id)}))
        )


async def opened(s: AsyncSession, node_id: uuid.UUID) -> None:
    """The learner opened this node's explanation (asked for it, or opened a node that has one).
    The node starts (design §5.5: not when a prefetch or a run wrote its explanation), and with
    `prefetch` (design §5.1) the node right after it in outline order is explained in the
    background if it has no explanation yet. Only that one node: opening the same node again
    must not walk the prefetch further, so cost follows the learner's progress. Commits."""
    node = await outline.get_node(s, node_id)
    if node.status == NodeStatus.NOT_STARTED:
        node.status = NodeStatus.LEARNING
    topic = await topics.get_topic(s, node.topic_id)
    if (
        topic.explain_mode == ExplainMode.PREFETCH
        and topic.outline_status == OutlineStatus.CONFIRMED
    ):
        ordered = [n.id for n in await outline.ordered_nodes(s, topic.id)]
        upcoming = ordered[ordered.index(node_id) + 1 : ordered.index(node_id) + 2]
        if upcoming and not await branches.explained_nodes(s, upcoming):
            await enqueue_explain(s, upcoming[0])
    await s.commit()


async def node_states(
    s: AsyncSession, node_ids: list[uuid.UUID]
) -> dict[uuid.UUID, ExplanationStatus]:
    """What each node page shows while explanations are made elsewhere (design §5.1.1), in three
    queries for any number of nodes."""
    explained = await branches.explained_nodes(s, node_ids)
    latest = dict(
        (
            await s.execute(
                select(Branch.node_id, Message.status)
                .join(Message, Message.branch_id == Branch.id)
                .where(Branch.node_id.in_(node_ids), *branches.MAIN_ANSWER)
                .ext(distinct_on(Branch.node_id))
                .order_by(Branch.node_id, Message.created_at.desc(), Message.id.desc())
            )
        ).all()
    )
    waiting = {
        uuid.UUID(n)
        for n in await s.scalars(
            select(Job.payload["node_id"].astext).where(
                Job.kind == JobKind.NODE_EXPLAIN,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
                Job.payload["node_id"].astext.in_([str(n) for n in node_ids]),
            )
        )
    }

    def state(node_id: uuid.UUID) -> ExplanationStatus:
        if node_id in explained:
            return "done"
        if latest.get(node_id) == MessageStatus.STREAMING or node_id in waiting:
            return "generating"
        return "failed" if node_id in latest else "none"

    return {n: state(n) for n in node_ids}


async def explanation_status(s: AsyncSession, node_id: uuid.UUID) -> ExplanationStatus:
    return (await node_states(s, [node_id]))[node_id]


async def estimate(s: AsyncSession, topic_id: uuid.UUID, *, default_tokens: int) -> Estimate:
    """Nodes still without an explanation times the median tokens of the last 50 explanations
    (design §5.1.1), or the configured default when there is no history yet."""
    pending = await _unexplained(s, topic_id)
    per_message = (
        select(func.sum(TOTAL_TOKENS))
        .where(UsageLog.role == "node.explain", UsageLog.message_id.is_not(None))
        .group_by(UsageLog.message_id)
        .order_by(func.max(UsageLog.created_at).desc())
        .limit(HISTORY)
    )
    history = [int(t) for t in await s.scalars(per_message)]
    per_node = int(statistics.median(history)) if history else default_tokens
    basis: Literal["history", "default"] = "history" if history else "default"
    return Estimate(len(pending), per_node, len(pending) * per_node, basis)


async def start_run(
    s: AsyncSession, topic_id: uuid.UUID, *, token_budget: int, default_tokens: int
) -> GenerationRun:
    topic = await topics.lock_topic(s, topic_id)
    if topic.outline_status != OutlineStatus.CONFIRMED:
        raise Unprocessable("confirm the outline first (design §5.1)")
    plan = await estimate(s, topic_id, default_tokens=default_tokens)
    if not plan.nodes:
        raise Unprocessable("every node is already explained")
    run = GenerationRun(
        id=uuid7(),
        topic_id=topic_id,
        node_ids=await _unexplained(s, topic_id),
        token_budget=token_budget,
        estimate=dataclasses.asdict(plan),
    )
    s.add(run)
    topic.explain_mode = ExplainMode.ALL
    try:
        await s.flush()
    except IntegrityError as exc:
        await s.rollback()
        raise Conflict("this topic already has a run in progress") from exc
    for node_id in run.node_ids:
        await enqueue_explain(s, node_id, run.id)
    await s.commit()
    return run


async def tokens_used(s: AsyncSession, run_id: uuid.UUID) -> int:
    used = await s.scalar(
        select(func.sum(TOTAL_TOKENS)).where(UsageLog.generation_run_id == run_id)
    )
    return int(used or 0)


async def get_run(s: AsyncSession, run_id: uuid.UUID) -> GenerationRun:
    return await get_or_404(s, GenerationRun, run_id)


async def latest_run_id(s: AsyncSession, topic_id: uuid.UUID) -> uuid.UUID | None:
    """The topic's most recent run, finished or not, so a reloaded client finds its progress."""
    return await s.scalar(
        select(GenerationRun.id)
        .where(GenerationRun.topic_id == topic_id)
        .order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1)
    )


async def admit(s: AsyncSession, run_id: uuid.UUID) -> Admission:
    """May the worker explain one more node of this run? `over` when the run ended, `wait` while
    it is paused or would go over budget (its jobs are then parked). Commits."""
    run = await s.get(GenerationRun, run_id, with_for_update=True)
    if run is None or run.status not in ACTIVE_RUN:
        return "over"
    if run.status == RunStatus.RUNNING:
        if await tokens_used(s, run_id) + int(run.estimate["tokens_per_node"]) > run.token_budget:
            run.status = RunStatus.BUDGET_EXHAUSTED  # nodes already running finish
            await s.execute(_waiting_jobs(run_id).values(run_after=text("'infinity'")))
    await s.commit()
    return "go" if run.status == RunStatus.RUNNING else "wait"


async def settle(s: AsyncSession, run_id: uuid.UUID) -> None:
    """`done` once every node is explained or its job gave up (design §5.1.1). Called when one of
    the run's jobs ends. Commits."""
    run = await s.get(GenerationRun, run_id, with_for_update=True)
    if run is None or run.status not in ACTIVE_RUN:
        return
    states = await node_states(s, run.node_ids)
    if all(st in ("done", "failed") for st in states.values()):
        run.status = RunStatus.DONE
    await s.commit()


async def change_run(
    s: AsyncSession, run_id: uuid.UUID, *, action: str | None, add_budget: int | None
) -> GenerationRun:
    run = await s.get(GenerationRun, run_id, with_for_update=True)
    if run is None:
        raise NotFound(f"generation run {run_id} not found")
    if run.status not in ACTIVE_RUN:
        raise Conflict(f"the run is {run.status.value}")
    if add_budget:
        run.token_budget += add_budget
        if run.status == RunStatus.BUDGET_EXHAUSTED:
            run.status = RunStatus.RUNNING
    if action == "pause":
        run.status = RunStatus.PAUSED
    elif action == "resume":
        run.status = RunStatus.RUNNING
    elif action == "cancel":
        run.status = RunStatus.CANCELLED
    if run.status == RunStatus.CANCELLED:
        # Jobs not yet taken are abandoned; a node already being explained finishes.
        await s.execute(_waiting_jobs(run_id).values(status=JobStatus.DEAD, last_error="cancelled"))
    elif run.status == RunStatus.RUNNING:
        await s.execute(_waiting_jobs(run_id).values(run_after=func.now()))  # wake them
    else:
        await s.execute(_waiting_jobs(run_id).values(run_after=text("'infinity'")))  # park them
    await s.commit()
    return run


async def retry_node(s: AsyncSession, run_id: uuid.UUID, node_id: uuid.UUID) -> GenerationRun:
    run = await get_run(s, run_id)
    if node_id not in run.node_ids:
        raise Unprocessable("the node is not part of this run")
    if run.status not in ACTIVE_RUN:
        raise Conflict(f"the run is {run.status.value}")
    await enqueue_explain(s, node_id, run.id)
    await s.commit()
    return run


async def _unexplained(s: AsyncSession, topic_id: uuid.UUID) -> list[uuid.UUID]:
    ordered = [n.id for n in await outline.ordered_nodes(s, topic_id)]
    explained = await branches.explained_nodes(s, ordered)
    return [n for n in ordered if n not in explained]


def _waiting_jobs(run_id: uuid.UUID) -> Update:
    return update(Job).where(
        Job.status == JobStatus.PENDING, Job.payload["run_id"].astext == str(run_id)
    )
