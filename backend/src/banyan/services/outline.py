"""The outline tree: add, edit, move and delete nodes (design §5.1 step 4).

Siblings are numbered 0..n-1 by `position`; every change renumbers the lists it touches. A topic's
outline is small (tens of nodes), so a change loads the topic's nodes and works in memory.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import Unprocessable
from banyan.ids import uuid7
from banyan.models import Branch, JobKind, Message, Node, NodeOrigin, enqueue
from banyan.models.tables import MAX_DEPTH
from banyan.services.lookup import get_or_404

Siblings = dict[uuid.UUID | None, list[Node]]


@dataclass(frozen=True)
class OutlineItem:
    title: str
    summary: str | None
    children: list["OutlineItem"]


async def topic_nodes(s: AsyncSession, topic_id: uuid.UUID) -> list[Node]:
    rows = await s.scalars(
        select(Node).where(Node.topic_id == topic_id).order_by(Node.depth, Node.position)
    )
    return list(rows)


async def get_node(s: AsyncSession, node_id: uuid.UUID) -> Node:
    return await get_or_404(s, Node, node_id)


async def add_node(
    s: AsyncSession,
    topic_id: uuid.UUID,
    *,
    title: str,
    summary: str | None,
    parent_id: uuid.UUID | None,
    position: int | None,
    origin: NodeOrigin,
) -> Node:
    nodes = await topic_nodes(s, topic_id)
    by_id = {n.id: n for n in nodes}
    depth = _child_depth(by_id, parent_id)
    siblings = _siblings(nodes)[parent_id]
    node = Node(
        topic_id=topic_id, parent_id=parent_id, depth=depth, title=title, summary=summary,
        origin=origin, position=0,
    )  # fmt: skip
    s.add(node)
    siblings.insert(_clamp(position, len(siblings)), node)
    _renumber(siblings)
    await s.commit()
    return node


async def update_node(s: AsyncSession, node_id: uuid.UUID, changes: dict[str, object]) -> Node:
    """`changes` holds only what the request set (NodePatch, exclude_unset)."""
    node = await get_node(s, node_id)
    for field, value in changes.items():
        setattr(node, field, value)
    await s.commit()
    return node


async def move_node(
    s: AsyncSession, node_id: uuid.UUID, *, parent_id: uuid.UUID | None, position: int
) -> Node:
    node = await get_node(s, node_id)
    nodes = await topic_nodes(s, node.topic_id)  # `node` is the same object (identity map)
    siblings = _siblings(nodes)
    below = _descendants(siblings, node)
    if parent_id is not None and (parent_id == node_id or parent_id in below):
        raise Unprocessable("a node cannot move under itself")
    shift = _child_depth({n.id: n for n in nodes}, parent_id) - node.depth
    if max(n.depth for n in [node, *below.values()]) + shift > MAX_DEPTH:
        raise Unprocessable(f"the outline is at most {MAX_DEPTH + 1} levels deep")

    old = siblings[node.parent_id]
    old.remove(node)
    _renumber(old)
    for n in [node, *below.values()]:
        n.depth += shift
    node.parent_id = parent_id
    target = [n for n in siblings[parent_id] if n is not node]
    target.insert(_clamp(position, len(target)), node)
    _renumber(target)
    await s.commit()
    return node


async def delete_node(
    s: AsyncSession, node_id: uuid.UUID, *, deprecate_entries: bool = False
) -> list[str]:
    """Deletes the node, its subtree, branches and messages. Returns the KB entries their answers
    proposed: they stay in steward unless the user asks to deprecate them too (design §4), which
    is queued in the same transaction."""
    node = await get_node(s, node_id)
    nodes = await topic_nodes(s, node.topic_id)
    siblings = _siblings(nodes)
    doomed = [node.id, *_descendants(siblings, node)]
    proposed = await s.scalars(
        select(Message.proposed_entry_ids)
        .join(Branch, Branch.id == Message.branch_id)
        .where(Branch.node_id.in_(doomed))
    )
    entry_ids = sorted({e for ids in proposed for e in ids})
    remaining = [n for n in siblings[node.parent_id] if n is not node]
    if deprecate_entries and entry_ids:
        await enqueue(
            s, JobKind.KB_DEPRECATE, key=f"kb_deprecate:node:{node_id}",
            payload={"entry_ids": entry_ids},
        )  # fmt: skip
    await s.delete(node)  # the database cascades to children, branches and messages
    _renumber(remaining)
    await s.commit()
    return entry_ids


async def replace_outline(
    s: AsyncSession, topic_id: uuid.UUID, items: list[OutlineItem]
) -> list[Node]:
    """Writes a generated outline (design §5.1 step 3), replacing any existing nodes. The caller
    commits, together with the usage rows of the call that produced it."""
    for old in await topic_nodes(s, topic_id):
        if old.parent_id is None:
            await s.delete(old)
    await s.flush()
    created: list[Node] = []

    def place(item: OutlineItem, parent: Node | None, position: int) -> None:
        depth = 0 if parent is None else parent.depth + 1
        if depth > MAX_DEPTH:
            return  # deeper levels are dropped: the outline is at most three levels
        node = Node(
            topic_id=topic_id, parent_id=parent.id if parent else None, depth=depth,
            position=position, title=item.title, summary=item.summary, origin=NodeOrigin.OUTLINE,
            id=uuid7(),  # set now: the children reference it before the flush
        )  # fmt: skip
        s.add(node)
        created.append(node)
        for i, child in enumerate(item.children):
            place(child, node, i)

    for i, item in enumerate(items):
        place(item, None, i)
    await s.flush()
    return created


def in_order(nodes: list[Node]) -> list[Node]:
    """Depth first, siblings by position: the order the learner studies them (design §5.1)."""
    children = _siblings(nodes)
    ordered: list[Node] = []

    def walk(parent: uuid.UUID | None) -> None:
        for n in children[parent]:
            ordered.append(n)
            walk(n.id)

    walk(None)
    return ordered


async def ordered_nodes(s: AsyncSession, topic_id: uuid.UUID) -> list[Node]:
    return in_order(await topic_nodes(s, topic_id))


def outline_text(nodes: list[Node]) -> str:
    """The outline as an indented list, for prompts."""
    return "\n".join(f"{'  ' * n.depth}- {n.title}" for n in in_order(nodes))


def _child_depth(by_id: dict[uuid.UUID, Node], parent_id: uuid.UUID | None) -> int:
    if parent_id is None:
        return 0
    parent = by_id.get(parent_id)
    if parent is None:
        raise Unprocessable(f"parent {parent_id} is not a node of this topic")
    if parent.depth >= MAX_DEPTH:
        raise Unprocessable(f"the outline is at most {MAX_DEPTH + 1} levels deep")
    return parent.depth + 1


def _siblings(nodes: list[Node]) -> Siblings:
    groups: Siblings = defaultdict(list)
    for n in sorted(nodes, key=lambda n: n.position):
        groups[n.parent_id].append(n)
    return groups


def _descendants(children: Siblings, root: Node) -> dict[uuid.UUID, Node]:
    found: dict[uuid.UUID, Node] = {}
    stack = list(children[root.id])
    while stack:
        n = stack.pop()
        found[n.id] = n
        stack.extend(children[n.id])
    return found


def _renumber(siblings: list[Node]) -> None:
    for i, n in enumerate(siblings):
        n.position = i


def _clamp(position: int | None, size: int) -> int:
    return size if position is None else max(0, min(position, size))
