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
    Node,
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
            # ... and to steward as an episode, whatever is left of it (design §8.5).
            await enqueue(
                s, JobKind.EPISODE_SUBMIT, key=f"episode_submit:close:{branch.id}:{uuid7()}",
                payload={"branch_id": str(branch.id)}, dedupe_key=f"episode:{branch.id}",
            )  # fmt: skip
        branch.closed_at = datetime.now(UTC) if closed else None
    await s.commit()
    return branch


# An answer that was answered again (design §6.3) is kept for display but is no longer current:
# it is never fed back to a model as history or notes material.
CURRENT = Message.superseded_by.is_(None)


async def get_answer(s: AsyncSession, message_id: uuid.UUID) -> Message:
    """A committed assistant answer, or NotFound."""
    message = await s.get(Message, message_id)
    if (
        message is None
        or message.role != MessageRole.ASSISTANT
        or message.status != MessageStatus.DONE
    ):
        raise NotFound(f"message {message_id} is not a committed answer")
    return message


def explain_question(node: Node) -> str:
    """What an explanation answers (design §5.2): the node itself."""
    return f'Explain "{node.title}"'


def after[M: Message](messages: list[M], mark: uuid.UUID | None) -> list[M]:
    """The messages after the one with id `mark` (a watermark of what was already taken in:
    notes, episodes); all of them when the mark is not among them."""
    ids = [m.id for m in messages]
    return messages[ids.index(mark) + 1 :] if mark in ids else messages


async def unsent(s: AsyncSession, branch: Branch) -> list[Message]:
    """The branch's current messages steward has not had in an episode (design §8.5). The
    watermark is found among all committed messages: it may be an answer since answered again."""
    done = await done_messages(s, branch.id)
    return [m for m in after(done, branch.episode_through) if m.superseded_by is None]


async def current_messages(s: AsyncSession, branch_id: uuid.UUID) -> list[Message]:
    """The branch's committed, current messages: what a model sees as its history."""
    return [m for m in await done_messages(s, branch_id) if m.superseded_by is None]


async def question_for(s: AsyncSession, answer: Message) -> str | None:
    """The question an answer replied to: the node itself on the main branch, otherwise the
    last current user message before it."""
    branch = await get_branch(s, answer.branch_id)
    if branch.kind == BranchKind.MAIN:
        return explain_question(await get_node(s, branch.node_id))
    earlier = [m for m in await current_messages(s, branch.id) if m.created_at < answer.created_at]
    return earlier[-1].content_md if earlier and earlier[-1].role == MessageRole.USER else None


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
    """The current explanation: a re-answered one is replaced by its successor (design §6.3)."""
    return await s.scalar(
        select(Message)
        .join(Branch, Branch.id == Message.branch_id)
        .where(
            Branch.node_id == node_id,
            Message.status == MessageStatus.DONE,
            CURRENT,
            *MAIN_ANSWER,
        )
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
