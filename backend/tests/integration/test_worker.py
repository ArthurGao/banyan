"""Banyan's handlers on the runtime: collection_ensure (design §5.1 step 6) and the sweeper."""

import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import update

from banyan.models import Job, JobKind, JobStatus, Message, MessageRole, MessageStatus, Topic
from banyan.steward import FakeSteward, StewardRejected, collection_name
from banyan.worker.sweep import sweep_stale_streams
from tests.conftest import Sessions, create_node, create_topic, rows, run_next, worker_ctx


async def test_collection_ensure_records_the_collection_once(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic = await create_topic(api)
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)
    assert await run_next(ctx) == "done"
    async with db() as s:
        saved = await s.get(Topic, uuid.UUID(topic["id"]))
        assert saved is not None
    assert saved.steward_collection_id == steward.collections[collection_name(topic["id"])]
    assert await run_next(ctx) is None  # nothing left


async def test_collection_ensure_skips_a_topic_that_has_one(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    topic = await create_topic(api)
    async with db() as s:
        await s.execute(update(Topic).values(steward_collection_id="known"))
        await s.commit()
    steward = FakeSteward()
    assert await run_next(worker_ctx(db, migrated_url, steward=steward)) == "done"
    assert steward.collections == {}
    assert topic["id"]


async def test_collection_ensure_retries_an_unavailable_steward_and_dies_on_a_rejection(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str, monkeypatch: object
) -> None:
    await create_topic(api)
    assert (
        await run_next(worker_ctx(db, migrated_url, steward=FakeSteward(unavailable=True)))
        == "pending"
    )

    rejecting = FakeSteward()

    async def reject(*_: object, **__: object) -> str:
        raise StewardRejected(403, "API key lacks the 'review' scope")

    rejecting.ensure_collection = reject  # type: ignore[method-assign]
    async with db() as s:
        await s.execute(update(Job).values(run_after=datetime.now(UTC)))
        await s.commit()
    assert await run_next(worker_ctx(db, migrated_url, steward=rejecting)) == "dead"
    (job,) = [j for j in await rows(db, Job) if j.kind == JobKind.COLLECTION_ENSURE]
    assert job.status == JobStatus.DEAD and "403" in (job.last_error or "")


async def test_without_steward_collection_jobs_wait(
    api: httpx.AsyncClient, db: Sessions, migrated_url: str
) -> None:
    await create_topic(api)
    assert await run_next(worker_ctx(db, migrated_url, steward=None)) is None


async def test_the_sweeper_fails_only_stale_streaming_answers(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "A")
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    other = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "R"})).json()
    async with db() as s:
        stale = Message(branch_id=uuid.UUID(branch["id"]), role=MessageRole.ASSISTANT,
                        status=MessageStatus.STREAMING)  # fmt: skip
        fresh = Message(branch_id=uuid.UUID(other["id"]), role=MessageRole.ASSISTANT,
                        status=MessageStatus.STREAMING)  # fmt: skip
        s.add_all([stale, fresh])
        await s.commit()
        await s.execute(
            update(Message).where(Message.id == stale.id)
            .values(updated_at=datetime.now(UTC) - timedelta(hours=1))
        )  # fmt: skip
        await s.commit()

    assert await sweep_stale_streams(db, older_than_s=600) == 1
    by_id = {m.id: m for m in await rows(db, Message)}
    assert by_id[stale.id].status == MessageStatus.FAILED
    assert by_id[stale.id].path[-1]["step"] == "interrupted"
    assert by_id[fresh.id].status == MessageStatus.STREAMING
    # The branch takes questions again.
    assert (await api.get(f"/v1/branches/{branch['id']}/messages/latest")).json()[
        "status"
    ] == "failed"
