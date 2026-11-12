"""Answers back to the KB (design §6.2 step 6, §6.3, §8.2): drafting an entry from an answer,
feedback on cited knowledge, and deprecating what a wrong answer proposed. Run by the worker; every
write to steward is idempotent by the job's key (AGENTS.md rule 6)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.llm import CallContext, Role, RoleClient, UsageCollector
from banyan.models import Branch, Feedback, Message, Node, Topic
from banyan.pipeline.record import add_usage
from banyan.pipeline.sources import SourceText, wrap
from banyan.prompts import render
from banyan.services import branches
from banyan.services.lookup import get_or_404
from banyan.steward import ProposedEntry, StewardClient, StewardRejected


class CollectionNotReady(Exception):
    """The topic's steward collection is not created yet (collection_ensure); try later."""


class DraftEntry(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=20_000)
    risk_level: Literal["low", "medium", "high"] = "low"


async def propose(
    sessions: async_sessionmaker[AsyncSession], llm: RoleClient, steward: StewardClient,
    message_id: uuid.UUID, *, idempotency_key: str,
) -> ProposedEntry | None:  # fmt: skip
    """Drafts an entry from the answer with `kb.draft_entry` and writes it to the topic's
    collection, tagged per design §8.2. None when there is nothing to propose: proposed before,
    marked wrong or answered again first (no draft is paid for). An answer marked wrong while it
    was being proposed has its new entry deprecated here, since its feedback found nothing to
    deprecate (design §6.3)."""
    async with sessions() as s:
        message, node, topic = await _answer(s, message_id)
        if (
            message.proposed_entry_ids
            or message.feedback == Feedback.WRONG
            or message.superseded_by is not None
        ):
            return None
        if not topic.steward_collection_id:
            raise CollectionNotReady(f"topic {topic.id} has no steward collection yet")
        question = await branches.question_for(s, message) or ""
        source = message.source.value if message.source else "llm"
    material = wrap([SourceText("Q", "kb", question), SourceText("A", "kb", message.content_md)])
    prompt = render(
        "kb_draft_entry", topic_title=topic.title, node_title=node.title,
        material=material.text, sources_rule=material.rule,
    )  # fmt: skip
    usage = UsageCollector()
    try:
        draft = await llm.structured(
            Role.KB_DRAFT_ENTRY, DraftEntry, instructions=prompt.instructions,
            prompt=prompt.prompt, usage=usage,
            context=CallContext(topic_id=topic.id, message_id=message_id, path_kind="kb_propose"),
        )  # fmt: skip
        tags = [
            "app:banyan", f"topic:{topic.id}", f"node:{node.id}", f"source:{source}",
            "author:ai", f"origin_message:{message_id}",
        ]  # fmt: skip
        # A source line for people and MCP readers of the entry (design §8.2).
        where = f"{topic.title} / {node.title}"
        content = f"{draft.content}\n\n---\nSource: a Banyan answer ({source}), {where}"
        proposed = await steward.create_entry(
            topic.steward_collection_id, title=draft.title, content=content, tags=tags,
            risk_level=draft.risk_level, idempotency_key=idempotency_key,
        )  # fmt: skip
    finally:
        async with sessions() as s:  # usage is kept whatever happened after the model answered
            add_usage(s, usage)
            await s.commit()
    if proposed.entry_id is not None:
        async with sessions() as s:
            fresh = await get_or_404(s, Message, message_id)
            fresh.proposed_entry_ids = [*fresh.proposed_entry_ids, proposed.entry_id]
            wrong = fresh.feedback == Feedback.WRONG
            await s.commit()
        if wrong:
            await deprecate(steward, [proposed.entry_id], idempotency_key=idempotency_key)
    return proposed


async def give_feedback(
    sessions: async_sessionmaker[AsyncSession], steward: StewardClient, message_id: uuid.UUID,
    *, helpful: bool, note: str | None, idempotency_key: str,
) -> int:  # fmt: skip
    """Feedback on each KB item the answer cited, by `retrieval_id + item_id` (design §6.3)."""
    async with sessions() as s:
        message = await get_or_404(s, Message, message_id)
        cited = [c for c in message.citations if c.get("kind") == "kb" and c.get("retrieval_id")]
    for c in cited:
        await steward.feedback(
            c["retrieval_id"], c["item_id"], helpful=helpful, note=note,
            idempotency_key=f"{idempotency_key}:{c['item_id']}",
        )  # fmt: skip
    return len(cited)


async def deprecate_proposed(
    sessions: async_sessionmaker[AsyncSession], steward: StewardClient, message_id: uuid.UUID,
    *, idempotency_key: str,
) -> int:  # fmt: skip
    """Deprecates the entries a wrong answer proposed (design §6.3)."""
    async with sessions() as s:
        entry_ids = list((await get_or_404(s, Message, message_id)).proposed_entry_ids)
    return await deprecate(steward, entry_ids, idempotency_key=idempotency_key)


async def deprecate(steward: StewardClient, entry_ids: list[str], *, idempotency_key: str) -> int:
    """Deprecates the given entries (also a deleted node's, with "also deprecate", design §4).
    An entry steward no longer has is skipped."""
    for entry_id in entry_ids:
        try:
            await steward.deprecate_entry(entry_id, idempotency_key=f"{idempotency_key}:{entry_id}")
        except StewardRejected as exc:
            if exc.status != 404:
                raise
    return len(entry_ids)


async def _answer(s: AsyncSession, message_id: uuid.UUID) -> tuple[Message, Node, Topic]:
    message = await branches.get_answer(s, message_id)
    branch = await get_or_404(s, Branch, message.branch_id)
    node = await get_or_404(s, Node, branch.node_id)
    return message, node, await get_or_404(s, Topic, node.topic_id)
