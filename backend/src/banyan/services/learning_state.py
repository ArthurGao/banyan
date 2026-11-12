"""The learning state (design §5.5): computed from the data, no model. It goes to steward as the
last message of an episode, so the memory engine can extract what the learner finds hard."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.models import (
    Branch,
    BranchKind,
    Feedback,
    Message,
    MessageRole,
    Node,
    NodeStatus,
    Topic,
)
from banyan.services.lookup import get_or_404

WEAKEST = 3
_WEAK = (Feedback.WRONG, Feedback.INCOMPLETE)


async def describe(s: AsyncSession, node_id: uuid.UUID) -> str:
    node = await get_or_404(s, Node, node_id)
    topic = await get_or_404(s, Topic, node.topic_id)
    asked = await s.scalar(
        select(func.count(Message.id))
        .join(Branch, Branch.id == Message.branch_id)
        .where(
            Branch.node_id == node_id,
            Branch.kind == BranchKind.QUESTION,
            Message.role == MessageRole.USER,
        )
    )
    marks = dict(
        (
            await s.execute(
                select(Message.feedback, func.count(Message.id))
                .join(Branch, Branch.id == Message.branch_id)
                .where(Branch.node_id == node_id, Message.feedback.in_(_WEAK))
                .group_by(Message.feedback)
            )
        ).all()
    )
    progress = dict(
        (
            await s.execute(
                select(Node.status, func.count(Node.id))
                .where(Node.topic_id == topic.id)
                .group_by(Node.status)
            )
        ).all()
    )
    weakest = (
        await s.execute(
            select(Node.title, func.count(Message.id).label("marks"))
            .join(Branch, Branch.node_id == Node.id)
            .join(Message, Message.branch_id == Branch.id)
            .where(Node.topic_id == topic.id, Message.feedback.in_(_WEAK))
            .group_by(Node.id, Node.title)
            .order_by(func.count(Message.id).desc(), Node.title)
            .limit(WEAKEST)
        )
    ).all()
    lines = [
        "Learning state (computed by Banyan from the learner's activity, not said by the learner):",
        f"Topic: {topic.title}",
        f"Node: {node.title} ({node.status.value.replace('_', ' ')}); questions asked on this "
        f"node: {asked or 0}; answers marked wrong: {marks.get(Feedback.WRONG, 0)}, "
        f"incomplete: {marks.get(Feedback.INCOMPLETE, 0)}",
        "Topic progress: "
        + ", ".join(
            f"{progress.get(status, 0)} {status.value.replace('_', ' ')}"
            for status in (NodeStatus.DONE, NodeStatus.LEARNING, NodeStatus.NOT_STARTED)
        ),
    ]
    if weakest:
        lines.append("Weakest nodes: " + ", ".join(f"{t} ({n} marks)" for t, n in weakest))
    return "\n".join(lines)
