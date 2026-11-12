"""Explanations made ahead (design §5.1, §5.1.1) — the B2 acceptance list, with a real worker loop
on the test database and FakeLLM."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from banyan.jobs import JobQueue
from banyan.jobs.runner import run_one
from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import (
    Branch,
    BranchKind,
    GenerationRun,
    Job,
    JobKind,
    JobStatus,
    Message,
    MessageRole,
    MessageStatus,
    RunStatus,
    UsageLog,
)
from banyan.services import runs
from banyan.worker.context import WorkerContext
from banyan.worker.handlers import handlers
from banyan.worker.run import queue_for
from banyan.worker.run import run as run_worker
from tests.conftest import (
    NOT_FRESH,
    Sessions,
    create_node,
    create_topic,
    rows,
    worker_ctx,
)

EXPLANATION = "An explanation of this node."


def explain_only() -> FakeLLM:
    return FakeLLM(default=lambda role, prompt: EXPLANATION, roles=[Role.NODE_EXPLAIN])


def run_ctx(db: Sessions, url: str, llm: FakeLLM, **settings: Any) -> WorkerContext:
    return worker_ctx(db, url, llm, **({"job_lease_s": 2.0, "worker_slots": 2} | settings))


async def drain(
    ctx: WorkerContext, done: Callable[[], Awaitable[bool]], limit_s: float = 10
) -> None:
    """Runs the worker until `done()` or the time limit."""
    stop = asyncio.Event()
    worker = asyncio.create_task(run_worker(ctx, stop))
    try:
        async with asyncio.timeout(limit_s):
            while not await done():  # noqa: ASYNC110 - polls database state, not an in-process event
                await asyncio.sleep(0.05)
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=5)


async def outline(api: httpx.AsyncClient, n: int, mode: str) -> tuple[str, list[str]]:
    topic = await create_topic(api)
    ids = [(await create_node(api, topic["id"], f"Node {i}"))["id"] for i in range(n)]
    confirmed = await api.post(
        f"/v1/topics/{topic['id']}/outline/confirm", json={"explain_mode": mode}
    )
    assert confirmed.status_code == 200, confirmed.text
    return topic["id"], ids


async def explanations(db: Sessions) -> dict[uuid.UUID, int]:
    """Committed explanations per node."""
    async with db() as s:
        found = await s.execute(
            select(Branch.node_id)
            .join(Message, Message.branch_id == Branch.id)
            .where(
                Branch.kind == BranchKind.MAIN,
                Message.role == MessageRole.ASSISTANT,
                Message.status == MessageStatus.DONE,
            )
        )
        counts: dict[uuid.UUID, int] = {}
        for (node_id,) in found.all():
            counts[node_id] = counts.get(node_id, 0) + 1
        return counts


async def run_status(api: httpx.AsyncClient, run_id: str) -> dict[str, Any]:
    body: dict[str, Any] = (await api.get(f"/v1/generation-runs/{run_id}")).json()
    return body


async def test_generate_all_explains_every_node_exactly_once(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 10, "all")
    started = await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})
    assert started.status_code == 201, started.text
    run = started.json()
    assert (run["status"], len(run["nodes"]), run["estimate"]["basis"]) == (
        "running",
        10,
        "default",
    )

    async def finished() -> bool:
        return (await run_status(api, run["id"]))["status"] == "done"

    await drain(run_ctx(db, migrated_url, explain_only()), finished)
    assert await explanations(db) == {uuid.UUID(n): 1 for n in nodes}
    final = await run_status(api, run["id"])
    assert {n["status"] for n in final["nodes"]} == {"done"}
    usage = [u for u in await rows(db, UsageLog) if u.role == "node.explain"]
    assert len(usage) == 10 and {str(u.generation_run_id) for u in usage} == {run["id"]}
    assert final["tokens_used"] == sum(
        (u.input_tokens or 0) + (u.output_tokens or 0) for u in usage
    )


async def test_a_killed_worker_mid_node_does_not_duplicate_it(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 3, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    # A worker took node 0, wrote its placeholder, and died: no heartbeat, no cleanup.
    dead = JobQueue(db, Job.__table__, worker_id="dead", lease_s=0.3,  # type: ignore[arg-type]
                    max_attempts=6)  # fmt: skip
    claimed = await dead.claim([JobKind.NODE_EXPLAIN.value])
    assert claimed is not None and claimed.payload["node_id"] == nodes[0]
    async with db() as s:
        branch = Branch(node_id=uuid.UUID(nodes[0]), kind=BranchKind.MAIN, title="Node 0")
        s.add(branch)
        await s.flush()
        s.add(Message(branch_id=branch.id, role=MessageRole.ASSISTANT,
                      status=MessageStatus.STREAMING,
                      updated_at=datetime.now(UTC) - timedelta(hours=1)))  # fmt: skip
        await s.commit()

    async def finished() -> bool:
        return (await run_status(api, run["id"]))["status"] == "done"

    ctx = run_ctx(db, migrated_url, explain_only(), job_lease_s=0.3, stream_stale_s=60)
    await drain(ctx, finished, limit_s=20)
    assert await explanations(db) == {uuid.UUID(n): 1 for n in nodes}
    statuses = sorted(m.status.value for m in await rows(db, Message))
    assert statuses == ["done", "done", "done", "failed"]  # the dead worker's placeholder


async def test_a_run_stops_at_its_budget_and_continues_when_it_grows(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str, fake_llm: FakeLLM
) -> None:
    llm = explain_only()
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    topic_id, nodes = await outline(api, 6, "all")
    # One explanation first: its usage becomes the per-node estimate (history).
    await api.post(f"/v1/nodes/{nodes[0]}/explain")
    per_node = (await api.get(f"/v1/topics/{topic_id}/generation/estimate")).json()
    assert per_node["basis"] == "history" and per_node["nodes"] == 5
    t = per_node["tokens_per_node"]
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs",
                          json={"token_budget": int(3.5 * t)})).json()  # fmt: skip

    async def exhausted() -> bool:
        return (await run_status(api, run["id"]))["status"] == "budget_exhausted"

    # One node at a time, so "a node fits" is decided on exact usage (design: running ones finish).
    ctx = run_ctx(db, migrated_url, llm, bulk_concurrency=1, worker_slots=1)
    await drain(ctx, exhausted)
    assert len(await explanations(db)) == 1 + 3

    grown = await api.patch(f"/v1/generation-runs/{run['id']}", json={"add_budget": 10 * t})
    assert grown.json()["status"] == "running"

    async def finished() -> bool:
        return (await run_status(api, run["id"]))["status"] == "done"

    await drain(ctx, finished)
    assert await explanations(db) == {uuid.UUID(n): 1 for n in nodes}


async def test_prefetch_explains_the_next_node_when_one_is_opened(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    topic_id, nodes = await outline(api, 3, "prefetch")
    await api.post(f"/v1/nodes/{nodes[0]}/explain")  # the learner opens node 0
    waiting = [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN]
    assert [j.payload["node_id"] for j in waiting] == [nodes[1]]
    assert (await api.get(f"/v1/nodes/{nodes[1]}")).json()["explanation_status"] == "generating"

    async def node_1_explained() -> bool:
        return uuid.UUID(nodes[1]) in await explanations(db)

    await drain(run_ctx(db, migrated_url, explain_only()), node_1_explained)
    opened = (await api.get(f"/v1/nodes/{nodes[1]}")).json()  # opening node 1 prefetches node 2
    assert opened["explanation_status"] == "done"
    pending = [j.payload["node_id"] for j in await rows(db, Job)
               if j.kind == JobKind.NODE_EXPLAIN and j.status == JobStatus.PENDING]  # fmt: skip
    assert pending == [nodes[2]]
    assert topic_id


async def test_manual_mode_prefetches_nothing(
    api: httpx.AsyncClient, db: Sessions, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    _, nodes = await outline(api, 2, "manual")
    await api.post(f"/v1/nodes/{nodes[0]}/explain")
    assert [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN] == []


async def test_pause_holds_the_run_and_resume_finishes_it(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 3, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    paused = await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "pause"})
    assert paused.json()["status"] == "paused"
    ctx = run_ctx(db, migrated_url, explain_only())

    async def never() -> bool:
        return False

    try:
        await drain(ctx, never, limit_s=0.6)
    except TimeoutError:
        pass
    assert await explanations(db) == {}
    await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "resume"})

    async def finished() -> bool:
        return (await run_status(api, run["id"]))["status"] == "done"

    await drain(ctx, finished, limit_s=5)  # resuming makes the waiting jobs due at once
    assert await explanations(db) == {uuid.UUID(n): 1 for n in nodes}


async def test_cancel_abandons_waiting_nodes(api: httpx.AsyncClient, db: Sessions) -> None:
    topic_id, _ = await outline(api, 3, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    cancelled = await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "cancel"})
    assert cancelled.json()["status"] == "cancelled"
    jobs = [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN]
    assert {(j.status, j.last_error) for j in jobs} == {(JobStatus.DEAD, "cancelled")}
    again = await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "resume"})
    assert again.status_code == 409
    # A new run can start once the old one is over.
    assert (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).status_code == 201


async def test_one_run_at_a_time_and_only_after_confirming(api: httpx.AsyncClient) -> None:
    topic_id, _ = await outline(api, 2, "all")
    assert (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).status_code == 201
    assert (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).status_code == 409
    draft = await create_topic(api)
    await create_node(api, draft["id"], "A")
    refused = await api.post(f"/v1/topics/{draft['id']}/generation-runs", json={})
    assert refused.status_code == 422


async def test_a_job_waits_while_the_node_is_explained_interactively(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 1, "all")
    async with db() as s:  # an interactive explanation is streaming on node 0
        branch = Branch(node_id=uuid.UUID(nodes[0]), kind=BranchKind.MAIN, title="Node 0")
        s.add(branch)
        await s.flush()
        s.add(
            Message(branch_id=branch.id, role=MessageRole.ASSISTANT, status=MessageStatus.STREAMING)
        )
        await s.commit()
    await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})
    ctx = run_ctx(db, migrated_url, explain_only())

    async def retried() -> bool:
        job = next(j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN)
        return job.last_error == "the node is being explained"

    await drain(ctx, retried)
    (job,) = [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN]
    assert (job.status, job.attempts) == (JobStatus.PENDING, 0)  # waiting, no attempt spent
    assert await explanations(db) == {}


async def test_a_job_for_an_explained_node_does_nothing(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    _, nodes = await outline(api, 1, "manual")
    await api.post(f"/v1/nodes/{nodes[0]}/explain")
    async with db() as s:
        await runs.enqueue_explain(s, uuid.UUID(nodes[0]))
        await s.commit()
    ctx = run_ctx(db, migrated_url, explain_only())

    async def job_done() -> bool:
        job = next(j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN)
        return job.status == JobStatus.DONE

    await drain(ctx, job_done)
    assert await explanations(db) == {uuid.UUID(nodes[0]): 1}


async def test_a_node_taken_before_cancel_is_not_explained_after_it(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 1, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    ctx = run_ctx(db, migrated_url, explain_only())
    queue = queue_for(ctx)
    claimed = await queue.claim([JobKind.NODE_EXPLAIN.value])  # taken just before the cancel
    assert claimed is not None
    await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "cancel"})
    assert await run_one(queue, handlers(ctx), claimed) == "done"
    assert await explanations(db) == {}
    assert nodes


async def test_pausing_parks_the_runs_jobs_and_resuming_wakes_them(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic_id, _ = await outline(api, 2, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "pause"})
    async with db() as s:
        parked = await s.scalars(
            select(func.isfinite(Job.run_after)).where(Job.kind == JobKind.NODE_EXPLAIN)
        )
        assert set(parked) == {False}  # run_after = infinity: never claimed, no polling
    await api.patch(f"/v1/generation-runs/{run['id']}", json={"action": "resume"})
    async with db() as s:
        due = await s.scalars(
            select(Job.run_after <= func.now()).where(Job.kind == JobKind.NODE_EXPLAIN)
        )
        assert set(due) == {True}


async def test_a_run_settles_when_its_last_node_dies_without_anyone_reading_it(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, _ = await outline(api, 1, "all")
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    failing = FakeLLM({Role.NODE_EXPLAIN: [ConnectionError("down")]}, roles=[Role.NODE_EXPLAIN])
    ctx = run_ctx(db, migrated_url, failing, job_max_attempts=1)

    async def run_over() -> bool:
        async with db() as s:
            found = await s.get(GenerationRun, uuid.UUID(run["id"]))
            await s.refresh(found)  # type: ignore[arg-type]
            return found is not None and found.status == RunStatus.DONE

    await drain(ctx, run_over)
    (job,) = [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN]
    assert job.status == JobStatus.DEAD


async def test_a_run_adopts_a_prefetch_job_already_waiting_for_its_node(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic_id, nodes = await outline(api, 2, "all")
    async with db() as s:  # prefetch queued node 1 before the run started
        await runs.enqueue_explain(s, uuid.UUID(nodes[1]))
        await s.commit()
    run = (await api.post(f"/v1/topics/{topic_id}/generation-runs", json={})).json()
    waiting = [j for j in await rows(db, Job) if j.kind == JobKind.NODE_EXPLAIN]
    assert {j.payload["node_id"]: j.payload.get("run_id") for j in waiting} == {
        nodes[0]: run["id"], nodes[1]: run["id"],
    }  # fmt: skip

    async def finished() -> bool:
        return (await run_status(api, run["id"]))["status"] == "done"

    await drain(run_ctx(db, migrated_url, explain_only()), finished)
    usage = [u for u in await rows(db, UsageLog) if u.role == "node.explain"]
    assert {str(u.generation_run_id) for u in usage} == {run["id"]}
