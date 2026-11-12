"""Quick ask (design §5.3): a question asked without a node. `node.classify` places it in the
outline, or suggests a node the outline lacks; code decides whether the placement is confident
(the threshold), and the learner confirms a new node. Then the question is asked in a new branch
there, through the normal ask (owner decision 2026-11-12: two requests, classify then quick-ask)."""

import uuid
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from banyan.errors import NotFound, RoleFailed
from banyan.llm import CallContext, Role, RoleClient, RoleError, UsageCollector
from banyan.models import NodeOrigin
from banyan.pipeline.record import add_usage
from banyan.pipeline.types import AskInput
from banyan.prompts import render
from banyan.services import answers, branches, outline, topics

BRANCH_TITLE = 80  # characters of the question that name its branch


class _SuggestedNode(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=2000)
    parent: str | None = None  # a label, like N2; null: the top level
    position: int = Field(default=0, ge=0)


class ClassifyVerdict(BaseModel):
    """`node.classify`'s structured output, in the outline's labels (N1…)."""

    node: str | None = None
    confidence: float = Field(ge=0, le=1)
    suggested_node: _SuggestedNode | None = None


@dataclass(frozen=True)
class NewNode:
    title: str
    summary: str
    parent_id: uuid.UUID | None
    position: int


@dataclass(frozen=True)
class Placement:
    node_id: uuid.UUID | None
    confidence: float
    confident: bool
    suggested_node: NewNode | None


async def classify(
    s: AsyncSession, llm: RoleClient, topic_id: uuid.UUID, question: str, *, threshold: float
) -> Placement:
    topic = await topics.get_topic(s, topic_id)
    # Labels, not ids: a model copies "N3" reliably and cannot name a node of another topic.
    labels = {
        f"N{i}": n
        for i, n in enumerate(outline.in_order(await outline.topic_nodes(s, topic_id)), 1)
    }
    listed = "\n".join(f"{'  ' * n.depth}{label}: {n.title}" for label, n in labels.items())
    prompt = render(
        "node_classify", topic_title=topic.title, language_name=topic.language.prompt_name,
        outline=listed, question=question,
    )  # fmt: skip
    usage = UsageCollector()
    try:
        verdict = await llm.structured(
            Role.NODE_CLASSIFY, ClassifyVerdict, instructions=prompt.instructions,
            prompt=prompt.prompt, context=CallContext(topic_id=topic_id, path_kind="classify"),
            usage=usage,
        )  # fmt: skip
    except RoleError as exc:
        raise RoleFailed(exc.kind, str(exc)) from exc
    finally:
        add_usage(s, usage)  # a failed attempt still cost tokens (design §4)
        await s.commit()
    node = labels.get(verdict.node) if verdict.node else None  # an unknown label is no node
    confident = node is not None and verdict.confidence >= threshold
    suggested = None
    if not confident and verdict.suggested_node is not None:
        offer = verdict.suggested_node
        parent = labels.get(offer.parent) if offer.parent else None
        suggested = NewNode(
            offer.title, offer.summary, parent.id if parent else None, offer.position
        )
    return Placement(node.id if node else None, verdict.confidence, confident, suggested)


async def start(
    s: AsyncSession, topic_id: uuid.UUID, question: str, rt: answers.Runtime, *,
    node_id: uuid.UUID | None, new_node: NewNode | None, allow_web: bool,
) -> AskInput:  # fmt: skip
    """The question in a new branch: under the node the learner chose, or under the node they
    confirmed adding (`origin=question`)."""
    await topics.get_topic(s, topic_id)
    if new_node is not None:
        node = await outline.add_node(
            s, topic_id, title=new_node.title, summary=new_node.summary or None,
            parent_id=new_node.parent_id, position=new_node.position, origin=NodeOrigin.QUESTION,
            commit=False,
        )  # fmt: skip
    else:
        assert node_id is not None
        node = await outline.get_node(s, node_id)
        if node.topic_id != topic_id:
            raise NotFound(f"node {node_id} is not in topic {topic_id}")
    # Node, branch and the answer's placeholder commit together: a refused ask leaves nothing.
    branch = await branches.create_branch(s, node.id, question[:BRANCH_TITLE], commit=False)
    return await answers.start_ask(s, branch.id, question, rt, allow_web=allow_web)
