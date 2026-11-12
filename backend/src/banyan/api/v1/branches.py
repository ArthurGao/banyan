import uuid

from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from banyan.api import sse
from banyan.api.deps import AnswerRuntime, Session
from banyan.api.schemas import AskBody, BranchDetail, BranchOut, BranchPatch, MessageOut
from banyan.services import answers, branches

router = APIRouter(tags=["branches"])


@router.get("/branches/{branch_id}")
async def get_branch(branch_id: uuid.UUID, s: Session) -> BranchDetail:
    branch = await branches.get_branch(s, branch_id)
    messages = await branches.done_messages(s, branch_id)
    return BranchDetail(
        branch=BranchOut.model_validate(branch),
        messages=[MessageOut.model_validate(m) for m in messages],
    )


@router.patch("/branches/{branch_id}")
async def update_branch(branch_id: uuid.UUID, body: BranchPatch, s: Session) -> BranchOut:
    changes = body.model_dump(exclude_unset=True)
    return BranchOut.model_validate(await branches.update_branch(s, branch_id, **changes))


@router.get("/branches/{branch_id}/messages/latest")
async def latest_message(branch_id: uuid.UUID, s: Session) -> MessageOut:
    """After a dropped stream: the newest answer in any state (design §9)."""
    return MessageOut.model_validate(await branches.latest_answer(s, branch_id))


@router.post(
    "/branches/{branch_id}/ask",
    response_class=EventSourceResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "path, token…, done (or error)"},
        409: {"description": "already answered or another answer is streaming on the branch"},
    },
)
async def ask(
    branch_id: uuid.UUID, body: AskBody, s: Session, rt: AnswerRuntime
) -> EventSourceResponse:
    """Answers a question in the branch through the ask pipeline (design §6), streamed."""
    inp = await answers.start_ask(
        s,
        branch_id,
        body.question,
        history_budget=rt.history_budget,
        allow_web=body.allow_web,
        episode_every=rt.episode_every,
    )
    return sse.stream(answers.stream_answer(inp, rt))
