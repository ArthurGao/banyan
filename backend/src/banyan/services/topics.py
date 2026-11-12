"""Topics: created synchronously, outline confirmed before any explanation (design §5.1)."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import Conflict, NotFound, Unprocessable
from banyan.models import (
    ExplainMode,
    Job,
    JobKind,
    Language,
    Level,
    Node,
    NodeStatus,
    OutlineStatus,
    Topic,
)
from banyan.services.lookup import get_or_404


@dataclass(frozen=True)
class TopicProgress:
    topic: Topic
    nodes: int
    done: int


async def create_topic(
    s: AsyncSession,
    *,
    title: str,
    goal: str | None,
    level: Level,
    language: Language,
    instructions: str | None,
) -> Topic:
    topic = Topic(
        title=title,
        goal=goal,
        level=level,
        language=language,
        instructions=instructions,
    )
    s.add(topic)
    await s.flush()
    # The topic's steward collection is created by the worker (design §5.1 step 6), in the same
    # transaction as the topic so it is never lost.
    s.add(
        Job(
            kind=JobKind.COLLECTION_ENSURE,
            payload={"topic_id": str(topic.id)},
            idempotency_key=f"collection_ensure:{topic.id}",
        )
    )
    await s.commit()
    return topic


async def get_topic(s: AsyncSession, topic_id: uuid.UUID) -> Topic:
    return await get_or_404(s, Topic, topic_id)


async def lock_topic(s: AsyncSession, topic_id: uuid.UUID) -> Topic:
    """The topic row locked until the transaction ends: confirming and replacing the outline are
    serialized on it."""
    topic = await s.scalar(select(Topic).where(Topic.id == topic_id).with_for_update())
    if topic is None:
        raise NotFound(f"topic {topic_id} not found")
    return topic


async def list_topics(s: AsyncSession) -> list[TopicProgress]:
    done = func.count(Node.id).filter(Node.status == NodeStatus.DONE)
    rows = await s.execute(
        select(Topic, func.count(Node.id), done)
        .outerjoin(Node, Node.topic_id == Topic.id)
        .group_by(Topic.id)
        .order_by(Topic.created_at.desc())
    )
    return [TopicProgress(topic, nodes, finished) for topic, nodes, finished in rows.all()]


async def update_topic(s: AsyncSession, topic_id: uuid.UUID, changes: dict[str, object]) -> Topic:
    """`changes` holds only what the request set (TopicPatch, exclude_unset)."""
    topic = await get_topic(s, topic_id)
    for field, value in changes.items():
        setattr(topic, field, value)
    await s.commit()
    return topic


async def confirm_outline(s: AsyncSession, topic_id: uuid.UUID, mode: ExplainMode) -> Topic:
    topic = await lock_topic(s, topic_id)
    if topic.outline_status == OutlineStatus.CONFIRMED:
        raise Conflict("the outline is already confirmed")
    has_nodes = await s.scalar(select(func.count(Node.id)).where(Node.topic_id == topic_id))
    if not has_nodes:
        raise Unprocessable("an empty outline cannot be confirmed")
    topic.outline_status = OutlineStatus.CONFIRMED
    topic.explain_mode = mode
    await s.commit()
    return topic
