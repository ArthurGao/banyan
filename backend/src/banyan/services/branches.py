"""Branches and their messages (design §5.3, §9)."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import NotFound
from banyan.ids import uuid7
from banyan.models import (
    Branch,
    BranchKind,
    JobKind,
    Message,
    MessageRole,
    MessageStatus,
    enqueue,
)
from banyan.services.lookup import get_or_404
from banyan.services.outline import get_node


async def get_branch(s: AsyncSession, branch_id: uuid.UUID) -> Branch:
    return await get_or_404(s, Branch, branch_id)


async def node_branches(s: AsyncSession, node_id: uuid.UUID) -> list[Branch]:
    rows = await s.scalars(
        select(Branch).where(Branch.node_id == node_id).order_by(Branch.created_at)
    )
    return list(rows)


async def create_branch(s: AsyncSession, node_id: uuid.UUID, title: str) -> Branch:
    await get_node(s, node_id)
    branch = Branch(node_id=node_id, kind=BranchKind.QUESTION, title=title)
    s.add(branch)
    await s.commit()
    return branch


async def update_branch(
    s: AsyncSession,
    branch_id: uuid.UUID,
    *,
    title: str | None = None,
    node_id: uuid.UUID | None = None,
    closed: bool | None = None,
) -> Branch:
    branch = await get_branch(s, branch_id)
    if title is not None:
        branch.title = title
    if node_id is not None:
        await get_node(s, node_id)
        branch.node_id = node_id
    if closed is not None:
        if closed and branch.closed_at is None:
            # A finished branch goes into the node's notes (design §5.4).
            await enqueue(
                s, JobKind.NOTES_UPDATE, key=f"notes_update:branch:{branch.id}:{uuid7()}",
                payload={"node_id": str(branch.node_id)}, dedupe_key=f"notes:{branch.node_id}",
            )  # fmt: skip
        branch.closed_at = datetime.now(UTC) if closed else None
    await s.commit()
    return branch


async def done_messages(s: AsyncSession, branch_id: uuid.UUID) -> list[Message]:
    """What a branch shows and exports: committed messages only (design §9)."""
    rows = await s.scalars(
        select(Message)
        .where(Message.branch_id == branch_id, Message.status == MessageStatus.DONE)
        .order_by(Message.created_at, Message.id)
    )
    return list(rows)


async def latest_answer(s: AsyncSession, branch_id: uuid.UUID) -> Message:
    """The newest assistant message in any state: how a client recovers after a dropped stream."""
    await get_branch(s, branch_id)
    message = await s.scalar(
        select(Message)
        .where(Message.branch_id == branch_id, Message.role == MessageRole.ASSISTANT)
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(1)
    )
    if message is None:
        raise NotFound(f"branch {branch_id} has no answer yet")
    return message


# A node's explanation: the first committed assistant answer on its main branch (design §3 terms).
MAIN_ANSWER = (Branch.kind == BranchKind.MAIN, Message.role == MessageRole.ASSISTANT)


async def explanation(s: AsyncSession, node_id: uuid.UUID) -> Message | None:
    return await s.scalar(
        select(Message)
        .join(Branch, Branch.id == Message.branch_id)
        .where(Branch.node_id == node_id, Message.status == MessageStatus.DONE, *MAIN_ANSWER)
        .order_by(Message.created_at, Message.id)
        .limit(1)
    )


async def explained_nodes(s: AsyncSession, node_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    rows = await s.scalars(
        select(Branch.node_id)
        .join(Message, Message.branch_id == Branch.id)
        .where(Branch.node_id.in_(node_ids), Message.status == MessageStatus.DONE, *MAIN_ANSWER)
    )
    return set(rows)
