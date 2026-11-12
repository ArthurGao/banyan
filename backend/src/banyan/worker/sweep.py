"""Answers left `streaming` by a process that died (design §9): without this their branch would
answer 409 forever. Run at startup and periodically by the worker."""

from datetime import timedelta

from sqlalchemy import func, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.models import Message, MessageStatus

INTERRUPTED = {"step": "interrupted", "status": "failed", "detail": {"reason": "process_ended"}}


async def sweep_stale_streams(
    sessions: async_sessionmaker[AsyncSession], *, older_than_s: float
) -> int:
    cutoff = func.now() - timedelta(seconds=older_than_s)  # the database clock, as for leases
    async with sessions() as s:
        result = await s.execute(
            update(Message)
            .where(Message.status == MessageStatus.STREAMING, Message.updated_at < cutoff)
            .values(status=MessageStatus.FAILED, path=Message.path.op("||")([INTERRUPTED]))
        )
        await s.commit()
    assert isinstance(result, CursorResult)
    return result.rowcount
