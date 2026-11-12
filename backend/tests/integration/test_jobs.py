"""The job runtime (design §4) on the real test database: claiming, leases, retries, caps."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from banyan.jobs import Claimed, JobQueue, Permanent, RetryLater
from banyan.jobs.runner import run_one, run_worker
from banyan.models import Job, JobKind, JobStatus
from tests.conftest import Sessions, rows

KIND = JobKind.COLLECTION_ENSURE.value


def queue(db: Sessions, worker: str = "w1", *, lease_s: float = 0.4, attempts: int = 3) -> JobQueue:
    return JobQueue(db, Job.__table__, worker_id=worker, lease_s=lease_s,  # type: ignore[arg-type]
                    max_attempts=attempts, base_backoff_s=10)  # fmt: skip


async def add(db: Sessions, key: str, kind: str = KIND, **values: object) -> uuid.UUID:
    async with db() as s:
        job = Job(kind=JobKind(kind), idempotency_key=key, payload={"k": key}, **values)
        s.add(job)
        await s.commit()
        return job.id


async def job(db: Sessions, job_id: uuid.UUID) -> Job:
    async with db() as s:
        found = await s.get(Job, job_id)
        assert found is not None
        return found


async def test_claim_takes_the_oldest_due_job_and_leases_it(db: Sessions) -> None:
    first = await add(db, "a")
    await add(db, "b")
    await add(db, "later", run_after=datetime.now(UTC) + timedelta(hours=1))
    claimed = await queue(db).claim([KIND])
    assert claimed is not None and (claimed.id, claimed.attempts) == (first, 1)
    row = await job(db, first)
    assert (row.status, row.attempts) == (JobStatus.RUNNING, 1)
    assert row.locked_by == claimed.token and claimed.token.startswith("w1#")


async def test_many_workers_never_claim_the_same_job(db: Sessions) -> None:
    for round_ in range(5):
        await add(db, f"only-{round_}")
        got = await asyncio.gather(*(queue(db, f"w{i}").claim([KIND]) for i in range(20)))
        assert len([c for c in got if c is not None]) == 1


async def test_an_expired_lease_is_taken_over_and_the_old_worker_loses_it(db: Sessions) -> None:
    job_id = await add(db, "a")
    a, b = queue(db, "w1"), queue(db, "w2")
    claimed = await a.claim([KIND])
    assert claimed is not None
    assert await b.claim([KIND]) is None  # lease still live
    await asyncio.sleep(0.5)  # w1 died: no heartbeat
    taken = await b.claim([KIND])
    assert taken is not None and (taken.id, taken.attempts) == (job_id, 2)
    assert await a.complete(claimed) is False  # w1's late completion changes nothing
    assert (await job(db, job_id)).locked_by == taken.token and taken.token.startswith("w2#")


async def test_a_heartbeat_keeps_a_long_handler_its_job(db: Sessions) -> None:
    await add(db, "slow")
    a, b = queue(db, "w1"), queue(db, "w2")
    claimed = await a.claim([KIND])
    assert claimed is not None
    stolen: list[Claimed | None] = []

    async def slow(_: Claimed) -> None:
        await asyncio.sleep(0.6)  # longer than the 0.4 s lease
        stolen.append(await b.claim([KIND]))

    assert await run_one(a, {KIND: slow}, claimed) == "done"
    assert stolen == [None]


async def test_a_failure_backs_off_then_dies_after_max_attempts(db: Sessions) -> None:
    job_id = await add(db, "a")
    q = queue(db, attempts=2)

    async def broken(_: Claimed) -> None:
        raise ConnectionError("steward down")

    claimed = await q.claim([KIND])
    assert claimed is not None
    assert await run_one(q, {KIND: broken}, claimed) == "pending"
    row = await job(db, job_id)
    assert row.last_error == "ConnectionError: steward down"
    assert row.run_after > datetime.now(UTC) + timedelta(seconds=5)  # backoff 10 s
    async with db() as s:  # due again
        await s.execute(update(Job).values(run_after=datetime.now(UTC)))
        await s.commit()
    claimed = await q.claim([KIND])
    assert claimed is not None and claimed.attempts == 2
    assert await run_one(q, {KIND: broken}, claimed) == "dead"


async def test_permanent_dies_at_once(db: Sessions) -> None:
    job_id = await add(db, "a")
    q = queue(db)

    async def rejected(_: Claimed) -> None:
        raise Permanent("steward said 403")

    claimed = await q.claim([KIND])
    assert claimed is not None
    assert await run_one(q, {KIND: rejected}, claimed) == "dead"
    assert (await job(db, job_id)).status == JobStatus.DEAD


async def test_no_retry_lands_beyond_the_24_hour_window(db: Sessions) -> None:
    job_id = await add(db, "a")
    async with db() as s:
        await s.execute(
            update(Job).values(
                created_at=datetime.now(UTC) - timedelta(hours=23, minutes=59, seconds=55)
            )
        )
        await s.commit()
    q = queue(db, attempts=6)
    claimed = await q.claim([KIND])
    assert claimed is not None
    assert await q.fail(claimed, ConnectionError("x")) == "dead"  # a 10 s backoff would pass 24 h
    assert (await job(db, job_id)).status == JobStatus.DEAD


async def test_retry_later_does_not_spend_an_attempt(db: Sessions) -> None:
    job_id = await add(db, "a")
    q = queue(db)

    async def busy(_: Claimed) -> None:
        raise RetryLater("node busy", after_s=30)

    claimed = await q.claim([KIND])
    assert claimed is not None
    assert await run_one(q, {KIND: busy}, claimed) == "pending"
    row = await job(db, job_id)
    assert (row.status, row.attempts) == (JobStatus.PENDING, 0)
    assert row.run_after > datetime.now(UTC) + timedelta(seconds=20)


async def test_a_capped_kind_runs_at_most_that_many_at_once(db: Sessions) -> None:
    explain = JobKind.NODE_EXPLAIN.value
    await add(db, "n1", explain)
    await add(db, "n2", explain)
    await add(db, "c1")
    q = queue(db)
    caps = {explain: 1}
    first = await q.claim([explain, KIND], caps)
    second = await q.claim([explain, KIND], caps)
    third = await q.claim([explain, KIND], caps)
    assert first is not None and second is not None
    assert [first.kind, second.kind] == [explain, KIND]
    assert third is None  # n2 waits for n1


async def test_kinds_without_a_handler_are_not_claimed(db: Sessions) -> None:
    await add(db, "p", JobKind.KB_PROPOSE.value)
    assert await queue(db).claim([KIND]) is None


async def test_one_pending_job_per_dedupe_key(db: Sessions) -> None:
    await add(db, "a", dedupe_key="notes:n1")
    with pytest.raises(IntegrityError):
        await add(db, "b", dedupe_key="notes:n1")


async def test_the_worker_loop_runs_jobs_until_stopped(db: Sessions) -> None:
    ids = [await add(db, f"j{i}") for i in range(3)]
    seen: list[str] = []

    async def handle(claimed: Claimed) -> None:
        seen.append(claimed.idempotency_key)

    stop = asyncio.Event()
    worker = asyncio.create_task(
        run_worker(queue(db), {KIND: handle}, slots=2, poll_s=0.05, stop=stop)
    )
    for _ in range(100):
        if len(seen) == 3:
            break
        await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(worker, timeout=2)
    assert sorted(seen) == ["j0", "j1", "j2"]
    assert {(await job(db, i)).status for i in ids} == {JobStatus.DONE}
    assert all(j.locked_by is None for j in await rows(db, Job))


async def test_one_job_runs_at_a_time_per_dedupe_key(db: Sessions) -> None:
    await add(db, "first", dedupe_key="notes:n1")
    q = queue(db)
    running = await q.claim([KIND])
    assert running is not None
    await add(db, "second", dedupe_key="notes:n1")  # allowed: the first is no longer pending
    assert await q.claim([KIND]) is None  # not while "first" holds a live lease
    await q.complete(running)
    later = await q.claim([KIND])
    assert later is not None and later.idempotency_key == "second"


async def test_a_cap_holds_under_concurrent_claims(db: Sessions) -> None:
    explain = JobKind.NODE_EXPLAIN.value
    for round_ in range(3):
        async with db() as s:  # a clean slate each round
            await s.execute(update(Job).values(status=JobStatus.DONE))
            await s.commit()
        for i in range(6):
            await add(db, f"n{round_}-{i}", explain)
        got = await asyncio.gather(
            *(queue(db, f"w{i}").claim([explain], {explain: 2}) for i in range(10))
        )
        assert len([c for c in got if c is not None]) == 2


async def test_a_reclaimed_job_is_fenced_from_its_previous_holder(db: Sessions) -> None:
    job_id = await add(db, "a")
    q = queue(db, "same-process")  # every slot of one process shares the worker id
    first = await q.claim([KIND])
    assert first is not None
    await asyncio.sleep(0.5)  # the first holder stalled past its lease
    second = await q.claim([KIND])
    assert second is not None and second.attempts == 2
    assert await q.heartbeat(first) is False
    assert await q.complete(first) is False  # the stale holder cannot finish the new claim
    assert (await job(db, job_id)).status == JobStatus.RUNNING
    assert await q.complete(second) is True


async def test_a_handler_that_lost_its_lease_is_cancelled(db: Sessions) -> None:
    job_id = await add(db, "a")
    q = queue(db, lease_s=0.3)
    claimed = await q.claim([KIND])
    assert claimed is not None
    cancelled = asyncio.Event()

    async def slow(_: Claimed) -> None:
        async with db() as s:  # another worker took the job over
            await s.execute(update(Job).values(locked_by="someone-else"))
            await s.commit()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    assert await run_one(q, {KIND: slow}, claimed) == "lost"
    assert cancelled.is_set()
    assert (await job(db, job_id)).locked_by == "someone-else"
