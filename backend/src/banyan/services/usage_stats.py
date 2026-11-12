"""What the KB saves (design §1 goal, §11.2 usage page): per topic, how many answers came from the
KB, the tokens each role spent, and an estimate of the tokens the KB answers saved."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.models import Branch, Message, MessageRole, MessageStatus, Node, Source, Topic, UsageLog
from banyan.services.runs import TOTAL_TOKENS

ANSWERING_ROLES = ("answer.compose", "node.explain")


@dataclass(frozen=True)
class TopicUsage:
    topic_id: uuid.UUID
    title: str
    answers: int
    kb_answers: int
    tokens_by_role: dict[str, int]
    estimated_tokens_saved: int

    @property
    def hit_rate(self) -> float:
        return self.kb_answers / self.answers if self.answers else 0.0


async def topic_usage(s: AsyncSession, topic_id: uuid.UUID | None = None) -> list[TopicUsage]:
    query = select(Topic).order_by(Topic.created_at)
    if topic_id is not None:
        query = query.where(Topic.id == topic_id)
    topics = list(await s.scalars(query))
    return [await _one(s, t) for t in topics]


async def _one(s: AsyncSession, topic: Topic) -> TopicUsage:
    by_source = dict(
        (
            await s.execute(
                select(Message.source, func.count())
                .join(Branch, Branch.id == Message.branch_id)
                .join(Node, Node.id == Branch.node_id)
                .where(
                    Node.topic_id == topic.id,
                    Message.role == MessageRole.ASSISTANT,
                    Message.status == MessageStatus.DONE,
                )
                .group_by(Message.source)
            )
        ).all()
    )
    by_role = {
        role: int(tokens)
        for role, tokens in (
            await s.execute(
                select(UsageLog.role, func.sum(TOTAL_TOKENS))
                .where(UsageLog.topic_id == topic.id)
                .group_by(UsageLog.role)
            )
        ).all()
    }
    per_answer = (
        select(func.sum(TOTAL_TOKENS).label("tokens"))
        .where(
            UsageLog.topic_id == topic.id,
            UsageLog.role.in_(ANSWERING_ROLES),
            UsageLog.message_id.is_not(None),
        )
        .group_by(UsageLog.message_id)
        .subquery()
    )
    average = await s.scalar(select(func.avg(per_answer.c.tokens)))
    kb = by_source.get(Source.KB, 0)
    # A KB answer costs a cheap judge and a cheap answer instead of the strong model: the saving is
    # estimated as what the strong model averaged on this topic (design §11.2).
    return TopicUsage(
        topic.id, topic.title, sum(by_source.values()), kb, by_role, int(kb * float(average or 0))
    )
