"""Exporting a topic as one HTML study guide (design §10.2). Rendering needs no model and takes
milliseconds, so it runs in the request (in a thread); the result is stored whole or not at all."""

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.export import ExportNode, render_study_guide
from banyan.jobs.queue import error_text
from banyan.models import Branch, Export, ExportStatus, Message, MessageStatus, Node, NodeNotes
from banyan.services import outline, topics
from banyan.services.lookup import get_or_404

log = logging.getLogger("banyan.exports")

Clock = Callable[[], datetime]


async def export_topic(
    s: AsyncSession, topic_id: uuid.UUID, *, clock: Clock = lambda: datetime.now(UTC)
) -> Export:
    topic = await topics.get_topic(s, topic_id)
    ordered = await outline.ordered_nodes(s, topic_id)
    stored = {
        n.node_id: n
        for n in await s.scalars(
            select(NodeNotes).where(NodeNotes.node_id.in_([n.id for n in ordered]))
        )
    }
    nodes = [_node(n, stored.get(n.id)) for n in ordered]
    sources = await _sources(s, topic_id)
    export = Export(
        topic_id=topic_id,
        notes_versions={str(n.id): stored[n.id].version for n in ordered if n.id in stored},
        status=ExportStatus.PENDING,
    )
    try:
        export.html = await asyncio.to_thread(
            render_study_guide, topic_title=topic.title, goal=topic.goal, nodes=nodes,
            sources=sources, generated_at=clock(),
        )  # fmt: skip
        export.status = ExportStatus.DONE
    except Exception as exc:
        # A template error or a malformed note: a failed export, never a half-written file.
        log.exception("export of topic %s failed", topic_id)
        export.status, export.error = ExportStatus.FAILED, error_text(exc)
    s.add(export)
    await s.commit()
    return export


async def get_export(s: AsyncSession, export_id: uuid.UUID) -> Export:
    return await get_or_404(s, Export, export_id)


def _node(node: Node, notes: NodeNotes | None) -> ExportNode:
    return ExportNode(
        title=node.title, depth=node.depth, summary=node.summary,
        notes=notes.content if notes else None, version=notes.version if notes else 0,
    )  # fmt: skip


async def _sources(s: AsyncSession, topic_id: uuid.UUID) -> dict[str, int]:
    """How the topic's committed answers were sourced (design §10.2 footer)."""
    rows = await s.execute(
        select(Message.source, func.count())
        .join(Branch, Branch.id == Message.branch_id)
        .join(Node, Node.id == Branch.node_id)
        .where(
            Node.topic_id == topic_id,
            Message.status == MessageStatus.DONE,
            Message.source.is_not(None),
        )
        .group_by(Message.source)
    )
    return {source.value: count for source, count in rows.all() if source is not None}
