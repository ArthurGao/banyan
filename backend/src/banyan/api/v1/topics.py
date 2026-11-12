import uuid

from fastapi import APIRouter, Request, status
from sse_starlette import EventSourceResponse

from banyan.api import sse
from banyan.api.deps import Session
from banyan.api.schemas import (
    ConfirmOutline,
    NodeCreate,
    NodeOut,
    TopicCreate,
    TopicDetail,
    TopicOut,
    TopicPatch,
    TopicSummary,
    TopicUsageOut,
)
from banyan.services import generation, outline, topics, usage_stats

router = APIRouter(tags=["topics"])


@router.post("/topics", status_code=status.HTTP_201_CREATED)
async def create_topic(body: TopicCreate, s: Session) -> TopicOut:
    topic = await topics.create_topic(s, **body.model_dump())
    return TopicOut.model_validate(topic)


@router.get("/topics")
async def list_topics(s: Session) -> list[TopicSummary]:
    return [
        TopicSummary(**TopicOut.model_validate(p.topic).model_dump(), nodes=p.nodes, done=p.done)
        for p in await topics.list_topics(s)
    ]


@router.get("/topics/{topic_id}")
async def get_topic(topic_id: uuid.UUID, s: Session) -> TopicDetail:
    topic = await topics.get_topic(s, topic_id)
    nodes = await outline.topic_nodes(s, topic_id)
    return TopicDetail(
        topic=TopicOut.model_validate(topic), nodes=[NodeOut.model_validate(n) for n in nodes]
    )


@router.patch("/topics/{topic_id}")
async def update_topic(topic_id: uuid.UUID, body: TopicPatch, s: Session) -> TopicOut:
    changes = body.model_dump(exclude_unset=True)
    return TopicOut.model_validate(await topics.update_topic(s, topic_id, changes))


@router.post(
    "/topics/{topic_id}/outline",
    response_class=EventSourceResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "outline, then done"}},
)
async def generate_outline(
    topic_id: uuid.UUID, request: Request, s: Session, replace: bool = False
) -> EventSourceResponse:
    """Generates the outline with `outline.generate` (SSE: `outline`, then `done`; `error` on a
    failed role). 409 when the outline is confirmed, or exists and `replace` is false."""
    await generation.check_can_generate(s, topic_id, replace=replace)
    state = request.app.state
    return sse.stream(generation.generate_outline(state.sessions, state.llm, topic_id))


@router.post("/topics/{topic_id}/outline/confirm")
async def confirm_outline(topic_id: uuid.UUID, body: ConfirmOutline, s: Session) -> TopicOut:
    return TopicOut.model_validate(await topics.confirm_outline(s, topic_id, body.explain_mode))


@router.post("/topics/{topic_id}/nodes", status_code=status.HTTP_201_CREATED)
async def add_node(topic_id: uuid.UUID, body: NodeCreate, s: Session) -> NodeOut:
    await topics.get_topic(s, topic_id)
    node = await outline.add_node(s, topic_id, **body.model_dump())
    return NodeOut.model_validate(node)


@router.get("/usage", tags=["usage"])
async def usage(s: Session, topic_id: uuid.UUID | None = None) -> list[TopicUsageOut]:
    """Per topic: KB hit rate, tokens by role, estimated tokens saved (design §11.2)."""
    return [TopicUsageOut.model_validate(u) for u in await usage_stats.topic_usage(s, topic_id)]
