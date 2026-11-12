"""Step 7, record (design §6.2, §9): the commit point. The answer, its usage rows and its jobs are
written in one transaction; a failed or aborted answer keeps its usage rows and enqueues nothing.

Every final state is a compare-and-set on `status = 'streaming'`: whichever write comes first wins
and the other changes nothing, so an abort racing a commit cannot half-apply either.
"""

import uuid
from typing import Any

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.llm import UsageCollector
from banyan.models import (
    ErrorKind,
    Job,
    JobKind,
    Message,
    MessageStatus,
    Node,
    NodeStatus,
    UsageLog,
)
from banyan.pipeline.events import PathEntry
from banyan.pipeline.types import AskInput, AskResult


async def done(
    sessions: async_sessionmaker[AsyncSession], inp: AskInput, result: AskResult,
    usage: UsageCollector, path: list[PathEntry],
) -> bool:  # fmt: skip
    """True when this call committed the answer."""
    async with sessions() as s:
        committed = await _finish(
            s, inp.message_id, MessageStatus.DONE, path,
            content_md=result.answer.content, source=result.answer.source,
            provenance=",".join(result.answer.roles),
            steward_retrieval_ids=result.retrieval_ids, cited_entry_ids=result.cited_entry_ids,
        )  # fmt: skip
        if not committed:
            return False
        add_usage(s, usage)
        for job in result.jobs:
            s.add(
                Job(
                    kind=JobKind(job.kind),
                    idempotency_key=job.idempotency_key,
                    payload=job.payload,
                    dedupe_key=job.dedupe_key,
                )
            )
        if inp.mode == "explain":
            # Opening an explanation starts the node (design §5.5).
            await s.execute(
                update(Node)
                .where(Node.id == inp.node_id, Node.status == NodeStatus.NOT_STARTED)
                .values(status=NodeStatus.LEARNING)
            )
        await s.commit()
        return True


async def ended(
    sessions: async_sessionmaker[AsyncSession],
    message_id: uuid.UUID,
    status: MessageStatus,
    usage: UsageCollector,
    path: list[PathEntry],
) -> None:
    """A failed or aborted answer: status, path and usage, never jobs (design §9)."""
    async with sessions() as s:
        if await _finish(s, message_id, status, path):
            add_usage(s, usage)
            await s.commit()


async def _finish(
    s: AsyncSession, message_id: uuid.UUID, status: MessageStatus, path: list[PathEntry],
    **values: Any,
) -> bool:  # fmt: skip
    result = await s.execute(
        update(Message)
        .where(Message.id == message_id, Message.status == MessageStatus.STREAMING)
        .values(status=status, path=[p.model_dump() for p in path], **values)
    )
    assert isinstance(result, CursorResult)
    return result.rowcount == 1


def add_usage(s: AsyncSession, usage: UsageCollector) -> None:
    """One row per attempt (design §4), in the caller's transaction."""
    for r in usage.records:
        s.add(
            UsageLog(
                topic_id=r.context.topic_id,
                message_id=r.context.message_id,
                role=r.role,
                provider=r.provider,
                model=r.model,
                attempt=r.attempt,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                path_kind=r.context.path_kind,
                ok=r.ok,
                error_kind=ErrorKind(r.error_kind) if r.error_kind else None,
                latency_ms=r.latency_ms,
            )
        )
