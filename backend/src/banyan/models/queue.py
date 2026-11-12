"""Enqueueing a job in the caller's transaction (design §4, AGENTS.md rule 6)."""

from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.ids import uuid7
from banyan.models.enums import JobKind, JobStatus
from banyan.models.tables import Job


async def enqueue(
    s: AsyncSession,
    kind: JobKind,
    *,
    key: str,
    payload: dict[str, Any],
    dedupe_key: str | None = None,
) -> None:
    """Adds the job unless its idempotency key exists or a job with its dedupe key is already
    waiting: a duplicate is merged away, never an error that rolls back the caller."""
    await s.execute(
        insert(Job)
        .values(
            id=uuid7(),
            kind=kind,
            payload=payload,
            idempotency_key=key,
            status=JobStatus.PENDING,
            attempts=0,
            dedupe_key=dedupe_key,
        )
        .on_conflict_do_nothing()
    )
