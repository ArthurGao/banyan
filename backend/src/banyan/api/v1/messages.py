"""Feedback on an answer, and re-answering (design §6.3)."""

import uuid

from fastapi import APIRouter
from sse_starlette import EventSourceResponse

from banyan.api import sse
from banyan.api.deps import AnswerRuntime, Session
from banyan.api.schemas import FeedbackIn, MessageOut
from banyan.models import Feedback
from banyan.services import answers, feedback

router = APIRouter(tags=["feedback"])


@router.post(
    "/messages/{message_id}/feedback",
    response_model=MessageOut,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "The answer as JSON, or the new answer's SSE (wrong/incomplete)",
        },
        409: {"description": "the answer was already answered again, or one is streaming"},
    },
)
async def give_feedback(
    message_id: uuid.UUID, body: FeedbackIn, s: Session, rt: AnswerRuntime
) -> MessageOut | EventSourceResponse:
    """good / wrong / incomplete. Wrong and incomplete answer again (force_llm) unless
    `reanswer` is false; the new answer supersedes this one when it commits."""
    verdict = Feedback(body.feedback)
    message = await feedback.record_feedback(s, message_id, verdict, body.note)
    if verdict == Feedback.GOOD or not body.reanswer:
        await s.commit()
        await s.refresh(message)
        return MessageOut.model_validate(message)
    # start_reanswer commits the verdict with the new answer's placeholder, or raises.
    inp = await answers.start_reanswer(s, message_id, history_budget=rt.history_budget)
    return sse.stream(answers.stream_answer(inp, rt))
