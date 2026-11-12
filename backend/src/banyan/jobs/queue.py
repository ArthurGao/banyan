"""Claim, heartbeat, complete and fail jobs in one table.

The table needs: id, kind, payload, idempotency_key, status (pending|running|done|failed|dead),
attempts, run_after, last_error, locked_at, locked_by, created_at, updated_at.
"""

import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Table, and_, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

MAX_ERROR_CHARS = 2000
CAP_LOCK = "banyan.jobs.cap"


class Permanent(Exception):
    """A failure no retry can fix (e.g. steward's 4xx, which it replays under the same key for
    24 hours, design §4): the job goes `dead` at once."""


class RetryLater(Exception):
    """Not a failure: the job cannot run yet (e.g. the node is busy). Back to `pending` after
    `after_s`, without spending an attempt."""

    def __init__(self, reason: str, *, after_s: float) -> None:
        super().__init__(reason)
        self.after_s = after_s


@dataclass(frozen=True)
class Claimed:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    idempotency_key: str
    attempts: int  # including this one
    created_at: datetime
    token: str  # this claim's lease holder: updates by an earlier claim of the job match nothing


class JobQueue:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        table: Table,
        *,
        worker_id: str,
        lease_s: float,
        max_attempts: int,
        retry_window_s: float = 24 * 3600,
        base_backoff_s: float = 5.0,
        max_backoff_s: float = 3600.0,
    ) -> None:
        self._sessions = sessions
        self._t = table
        self.worker_id = worker_id
        self.lease = timedelta(seconds=lease_s)
        self.max_attempts = max_attempts
        # Every retry ends within this window of the job's creation: steward keeps an
        # Idempotency-Key for 24 hours (design §4).
        self.retry_window = timedelta(seconds=retry_window_s)
        self.base_backoff_s = base_backoff_s
        self.max_backoff_s = max_backoff_s

    async def claim(
        self, kinds: Collection[str], caps: Mapping[str, int] | None = None
    ) -> Claimed | None:
        """The next due job of `kinds`, or a running one whose lease expired. Not claimed: a kind
        already running `caps[kind]` jobs, and a job whose dedupe key has another job running
        (one at a time per key, e.g. one notes update per node)."""
        t = self._t
        live = t.c.locked_at >= func.now() - self.lease
        other = t.alias("other")
        token = f"{self.worker_id}#{uuid.uuid4().hex[:12]}"
        async with self._sessions() as s:
            if caps and set(kinds) & set(caps):
                # Counting and claiming happen under one lock, or two claims could both see room
                # under a cap and both take a job. Held until this transaction ends.
                await s.execute(select(func.pg_advisory_xact_lock(func.hashtext(CAP_LOCK))))
            allowed = set(kinds) - await self._at_cap(s, caps or {}, live)
            if not allowed:
                return None
            row = (
                await s.execute(
                    select(t)
                    .where(
                        t.c.kind.in_(sorted(allowed)),
                        or_(
                            and_(t.c.status == "pending", t.c.run_after <= func.now()),
                            and_(t.c.status == "running", ~live),
                        ),
                        ~exists().where(
                            other.c.dedupe_key == t.c.dedupe_key,
                            other.c.id != t.c.id,
                            other.c.status == "running",
                            other.c.locked_at >= func.now() - self.lease,
                        ),
                    )
                    .order_by(t.c.run_after, t.c.created_at)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).first()
            if row is None:
                return None
            # Compare-and-set on what was read: even if two claims ever read the same row, only
            # one update matches.
            taken = await s.execute(
                update(t)
                .where(t.c.id == row.id, t.c.status == row.status, t.c.attempts == row.attempts)
                .values(
                    status="running",
                    locked_at=func.now(),
                    locked_by=token,
                    attempts=row.attempts + 1,
                    updated_at=func.now(),
                )
            )
            await s.commit()
        if not getattr(taken, "rowcount", 0):
            return None
        return Claimed(
            row.id, row.kind, row.payload, row.idempotency_key, row.attempts + 1, row.created_at,
            token,
        )  # fmt: skip

    async def heartbeat(self, job: Claimed) -> bool:
        """Extends the lease; False when another worker took the job over (lease expired)."""
        return await self._update(job, locked_at=func.now(), updated_at=func.now())

    async def complete(self, job: Claimed) -> bool:
        return await self._update(
            job, status="done", locked_at=None, locked_by=None, last_error=None,
            updated_at=func.now(),
        )  # fmt: skip

    async def retry_later(self, job: Claimed, exc: RetryLater) -> bool:
        return await self._update(
            job, status="pending", attempts=job.attempts - 1, locked_at=None, locked_by=None,
            run_after=func.now() + timedelta(seconds=exc.after_s),
            last_error=str(exc)[:MAX_ERROR_CHARS], updated_at=func.now(),
        )  # fmt: skip

    async def fail(self, job: Claimed, exc: BaseException) -> str:
        """Back to `pending` with backoff, or `dead`: permanent, out of attempts, or a retry
        that would land outside the window. Returns the new status."""
        delay = timedelta(seconds=self.backoff_s(job.attempts))
        async with self._sessions() as s:
            now = await s.scalar(select(func.now()))  # the database clock, as for leases
            assert isinstance(now, datetime)
        dead = (
            isinstance(exc, Permanent)
            or job.attempts >= self.max_attempts
            or now + delay > job.created_at + self.retry_window
        )
        status = "dead" if dead else "pending"
        await self._update(
            job, status=status, locked_at=None, locked_by=None, last_error=error_text(exc),
            run_after=func.now() if dead else func.now() + delay, updated_at=func.now(),
        )  # fmt: skip
        return status

    def backoff_s(self, attempts: int) -> float:
        return float(min(self.base_backoff_s * 2 ** max(attempts - 1, 0), self.max_backoff_s))

    async def _update(self, job: Claimed, **values: Any) -> bool:
        """Only while this claim still holds the job: a reclaimed job is no longer ours, even for
        another slot of the same worker."""
        t = self._t
        async with self._sessions() as s:
            result = await s.execute(
                update(t)
                .where(t.c.id == job.id, t.c.locked_by == job.token, t.c.status == "running")
                .values(**values)
            )
            await s.commit()
        return bool(getattr(result, "rowcount", 0))

    async def _at_cap(self, s: AsyncSession, caps: Mapping[str, int], live: Any) -> set[str]:
        if not caps:
            return set()
        t = self._t
        running = await s.execute(
            select(t.c.kind, func.count())
            .where(t.c.kind.in_(list(caps)), t.c.status == "running", live)
            .group_by(t.c.kind)
        )
        return {kind for kind, n in running.all() if n >= caps[kind]}


def error_text(exc: BaseException) -> str:
    """How a failure is stored: type and message, bounded (no tracebacks, no secrets in URLs)."""
    return f"{type(exc).__name__}: {exc}"[:MAX_ERROR_CHARS]
