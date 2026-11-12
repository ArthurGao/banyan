"""Node notes (design §5.4, §10.1): `notes.update` proposes notes from new material; code merges
them so nothing already noted is lost, and saves with an optimistic version check.

New material is what the notes do not include yet: the node's explanation and its closed question
branches, tracked by id in `incorporated`. So an update triggered twice, or two triggers merged into
one job (dedupe), still takes in everything exactly once.
"""

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.llm import CallContext, Role, RoleClient, UsageCollector
from banyan.models import Branch, BranchKind, Feedback, Message, MessageStatus, NodeNotes
from banyan.pipeline.record import add_usage
from banyan.pipeline.sources import SourceText, wrap
from banyan.prompts import render
from banyan.services import branches, outline, topics


class Example(BaseModel):
    title: str = Field(max_length=300)
    body_md: str = Field(max_length=8000)


class Highlight(BaseModel):
    branch_id: str
    question: str = Field(max_length=4000)
    answer_md: str = Field(max_length=8000)
    message_id: str | None = None


class NotesContent(BaseModel):
    summary: str = Field(default="", max_length=4000)
    key_points: list[str] = Field(default_factory=list, max_length=60)
    examples: list[Example] = Field(default_factory=list, max_length=30)
    pitfalls: list[str] = Field(default_factory=list, max_length=40)
    branch_highlights: list[Highlight] = Field(default_factory=list, max_length=60)


# What the notes already include: the explanation's message id, and per question branch the last
# message taken in.
Incorporated = dict[str, Any]


class VersionConflict(Exception):
    """Another update saved first; the job runs again on the new version."""


async def get_notes(s: AsyncSession, node_id: uuid.UUID) -> NodeNotes | None:
    return await s.scalar(select(NodeNotes).where(NodeNotes.node_id == node_id))


async def update_notes(
    sessions: async_sessionmaker[AsyncSession], llm: RoleClient, node_id: uuid.UUID
) -> bool:
    """True when the notes changed; False when there was nothing new."""
    async with sessions() as s:
        node = await outline.get_node(s, node_id)
        topic = await topics.get_topic(s, node.topic_id)
        current = await get_notes(s, node_id)
        material, incorporated = await _material(
            s, node_id, current.incorporated if current else {}
        )
        if not material:
            return False
        before = NotesContent.model_validate(current.content) if current else NotesContent()
        wrapped = wrap(material)
        prompt = render(
            "notes_update", language_name=topic.language.prompt_name, topic_title=topic.title,
            node_title=node.title, node_summary=node.summary,
            current=before.model_dump_json(indent=1), material=wrapped.text,
            sources_rule=wrapped.rule,
        )  # fmt: skip
        version = current.version if current else 0
    usage = UsageCollector()
    try:
        proposed = await llm.structured(
            Role.NOTES_UPDATE, NotesContent, instructions=prompt.instructions,
            prompt=prompt.prompt, context=CallContext(topic_id=topic.id, path_kind="notes"),
            usage=usage,
        )  # fmt: skip
    except BaseException:
        async with sessions() as s:  # a failed attempt still cost tokens (design §4)
            add_usage(s, usage)
            await s.commit()
        raise

    merged = merge(before, proposed)
    async with sessions() as s:
        add_usage(s, usage)
        saved = await _save(s, node_id, version, merged, incorporated)
        await s.commit()  # usage is kept even when the save lost the race
    if not saved:
        raise VersionConflict(f"notes of node {node_id} changed meanwhile")
    return True


def merge(before: NotesContent, proposed: NotesContent) -> NotesContent:
    """Code decides what survives (design §10.1): nothing already noted is dropped; a branch's
    highlight is replaced by a newer one for the same branch."""
    highlights = {h.branch_id: h for h in before.branch_highlights}
    highlights.update({h.branch_id: h for h in proposed.branch_highlights})
    return NotesContent(
        summary=proposed.summary or before.summary,
        key_points=_union(before.key_points, proposed.key_points),
        examples=list(
            {e.title: e for e in [*proposed.examples[::-1], *before.examples[::-1]]}.values()
        )[::-1],
        pitfalls=_union(before.pitfalls, proposed.pitfalls),
        branch_highlights=list(highlights.values()),
    )


async def _save(
    s: AsyncSession, node_id: uuid.UUID, version: int, content: NotesContent,
    incorporated: Incorporated,
) -> bool:  # fmt: skip
    values = {"content": content.model_dump(), "incorporated": incorporated}
    if version == 0:
        try:
            async with s.begin_nested():
                s.add(NodeNotes(node_id=node_id, version=1, **values))
        except IntegrityError:
            return False
        return True
    result = await s.execute(
        update(NodeNotes)
        .where(NodeNotes.node_id == node_id, NodeNotes.version == version)
        .values(version=version + 1, **values)
    )
    return bool(getattr(result, "rowcount", 0))


_marked_good = select(Message.branch_id).where(Message.feedback == Feedback.GOOD, branches.CURRENT)


async def _material(
    s: AsyncSession, node_id: uuid.UUID, seen: Incorporated
) -> tuple[list[SourceText], Incorporated]:
    """What the notes do not include yet, and the watermarks once it is in: the explanation (by
    message id) and, per closed (or marked-good) question branch, the turns after the last one
    taken in. A branch
    reopened, continued and closed again brings only its new turns."""
    material: list[SourceText] = []
    marks: dict[str, str] = dict(seen.get("branches", {}))
    explained = await branches.explanation(s, node_id)
    explanation_id = str(explained.id) if explained else seen.get("explanation")
    if explained is not None and explanation_id != seen.get("explanation"):
        material.append(SourceText(explanation_id or "", "kb", explained.content_md))
    rows = await s.execute(
        select(Branch, Message)
        .join(Message, Message.branch_id == Branch.id)
        .where(
            Branch.node_id == node_id,
            Branch.kind == BranchKind.QUESTION,
            # A finished branch, or one where the learner marked an answer good (design §5.4).
            or_(Branch.closed_at.is_not(None), Branch.id.in_(_marked_good)),
            Message.status == MessageStatus.DONE,
            branches.CURRENT,
        )
        .order_by(Branch.created_at, Message.created_at, Message.id)
    )
    turns: dict[uuid.UUID, list[Message]] = {}
    titles: dict[uuid.UUID, str] = {}
    for branch, message in rows.all():
        turns.setdefault(branch.id, []).append(message)
        titles[branch.id] = branch.title
    for branch_id, messages in turns.items():
        ids = [str(m.id) for m in messages]
        mark = marks.get(str(branch_id))
        new = messages[ids.index(mark) + 1 :] if mark in ids else messages
        if new:
            lines = [f"Question branch {branch_id}: {titles[branch_id]}"]
            lines += [f"{m.role.value}: {m.content_md}" for m in new]
            material.append(SourceText(str(branch_id), "kb", "\n".join(lines)))
            marks[str(branch_id)] = ids[-1]
    return material, {"explanation": explanation_id, "branches": marks}


def _union(old: list[str], new: list[str]) -> list[str]:
    seen = {p.strip().casefold() for p in old}
    return old + [p for p in new if p.strip().casefold() not in seen]
