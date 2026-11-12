import uuid

from fastapi import APIRouter, status
from sse_starlette import EventSourceResponse

from banyan.api import sse
from banyan.api.deps import AnswerRuntime, Session
from banyan.api.schemas import (
    BranchCreate,
    BranchOut,
    MessageOut,
    NodeDeleted,
    NodeDetail,
    NodeMove,
    NodeOut,
    NodePatch,
)
from banyan.services import answers, branches, outline

router = APIRouter(tags=["nodes"])


@router.get("/nodes/{node_id}")
async def get_node(node_id: uuid.UUID, s: Session) -> NodeDetail:
    node = await outline.get_node(s, node_id)
    explanation = await branches.explanation(s, node_id)
    return NodeDetail(
        node=NodeOut.model_validate(node),
        explanation=MessageOut.model_validate(explanation) if explanation else None,
        branches=[BranchOut.model_validate(b) for b in await branches.node_branches(s, node_id)],
    )


@router.patch("/nodes/{node_id}")
async def update_node(node_id: uuid.UUID, body: NodePatch, s: Session) -> NodeOut:
    node = await outline.update_node(s, node_id, body.model_dump(exclude_unset=True))
    return NodeOut.model_validate(node)


@router.post("/nodes/{node_id}/move")
async def move_node(node_id: uuid.UUID, body: NodeMove, s: Session) -> NodeOut:
    node = await outline.move_node(s, node_id, parent_id=body.parent_id, position=body.position)
    return NodeOut.model_validate(node)


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: uuid.UUID, s: Session) -> NodeDeleted:
    return NodeDeleted(proposed_entry_ids=await outline.delete_node(s, node_id))


@router.post("/nodes/{node_id}/branches", status_code=status.HTTP_201_CREATED)
async def create_branch(node_id: uuid.UUID, body: BranchCreate, s: Session) -> BranchOut:
    return BranchOut.model_validate(await branches.create_branch(s, node_id, body.title))


@router.post(
    "/nodes/{node_id}/explain",
    response_class=EventSourceResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "path, token…, done (or error)"},
        409: {"description": "already answered or another answer is streaming on the branch"},
    },
)
async def explain_node(node_id: uuid.UUID, s: Session, rt: AnswerRuntime) -> EventSourceResponse:
    """Explains the node with `node.explain` on its main branch (design §5.2), streamed."""
    inp = await answers.start_explain(s, node_id)
    return sse.stream(answers.stream_answer(inp, rt))
