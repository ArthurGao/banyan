"""Outline generation (design §5.1 step 3): `outline.generate` returns a tree that replaces the
topic's draft outline."""

import asyncio
import uuid
from collections.abc import AsyncIterator

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.errors import Conflict
from banyan.llm import CallContext, Role, RoleClient, RoleError, UsageCollector
from banyan.models import OutlineStatus
from banyan.pipeline.events import (
    DoneEvent,
    ErrorEvent,
    Event,
    OutlineEvent,
    PathEntry,
    PathEvent,
)
from banyan.pipeline.record import add_usage
from banyan.prompts import render
from banyan.services import outline, topics


class OutlineLeaf(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(max_length=2000)


class OutlineBranch(OutlineLeaf):
    children: list[OutlineLeaf] = Field(default_factory=list, max_length=20)


class OutlineTop(OutlineLeaf):
    children: list[OutlineBranch] = Field(default_factory=list, max_length=20)


class GeneratedOutline(BaseModel):
    """Three explicit levels (design §4: depth 0..2) rather than a recursive schema, which some
    providers' structured output handles badly."""

    nodes: list[OutlineTop] = Field(min_length=1, max_length=30)


async def check_can_generate(s: AsyncSession, topic_id: uuid.UUID, *, replace: bool) -> None:
    """Before the stream opens, so refusals are plain HTTP errors (design §9)."""
    topic = await topics.get_topic(s, topic_id)
    if topic.outline_status == OutlineStatus.CONFIRMED:
        raise Conflict("the outline is confirmed; it can no longer be regenerated")
    if not replace and await outline.topic_nodes(s, topic_id):
        raise Conflict("the topic already has an outline; pass replace=true to regenerate it")


async def generate_outline(
    sessions: async_sessionmaker[AsyncSession], llm: RoleClient, topic_id: uuid.UUID
) -> AsyncIterator[Event]:
    usage = UsageCollector()
    async with sessions() as s:
        topic = await topics.get_topic(s, topic_id)
        prompt = render(
            "outline_generate",
            title=topic.title,
            goal=topic.goal,
            level=topic.level.value,
            instructions=topic.instructions,
            language_name=topic.language.prompt_name,
        )
    yield PathEvent(PathEntry(step="outline", status="running"))

    async def save_usage() -> None:
        async with sessions() as s:
            add_usage(s, usage)
            await s.commit()

    try:
        generated = await llm.structured(
            Role.OUTLINE_GENERATE,
            GeneratedOutline,
            instructions=prompt.instructions,
            prompt=prompt.prompt,
            context=CallContext(topic_id=topic_id, path_kind="outline"),
            usage=usage,
        )
    except RoleError as exc:
        await save_usage()
        yield ErrorEvent(f"role_{exc.kind}", str(exc))
        return
    except (asyncio.CancelledError, GeneratorExit):
        await asyncio.shield(save_usage())  # the client left; the attempt still cost tokens
        raise
    async with sessions() as s:
        # Locked and checked again: the outline may have been confirmed while the model answered.
        topic = await topics.lock_topic(s, topic_id)
        if topic.outline_status == OutlineStatus.CONFIRMED:
            add_usage(s, usage)
            await s.commit()
            yield ErrorEvent("conflict", "the outline was confirmed meanwhile; it was kept")
            return
        nodes = await outline.replace_outline(s, topic_id, _items(generated))
        add_usage(s, usage)
        await s.commit()
    yield OutlineEvent(nodes)
    yield DoneEvent(None, None, usage.input_tokens, usage.output_tokens)


def _items(generated: GeneratedOutline) -> list[outline.OutlineItem]:
    def item(node: OutlineLeaf) -> outline.OutlineItem:
        children = getattr(node, "children", [])
        return outline.OutlineItem(node.title, node.summary, [item(c) for c in children])

    return [item(n) for n in generated.nodes]
