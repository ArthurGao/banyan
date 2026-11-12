"""The memory page (design §8.5, §9): memories and KB candidate cards, managed in steward."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from banyan.api.deps import Steward, User
from banyan.api.schemas import (
    CandidateDecision,
    CandidateOut,
    CandidatePageOut,
    MemoryOut,
    MemoryPageOut,
    MemoryPatch,
)
from banyan.services import memory

router = APIRouter(tags=["memory"])
UNAVAILABLE: dict[int | str, dict[str, Any]] = {
    503: {"description": "steward is unreachable or not configured"}
}


Limit = Annotated[int, Query(ge=1, le=200)]


@router.get("/memory", responses=UNAVAILABLE)
async def list_memories(
    steward: Steward, user: User, cursor: uuid.UUID | None = None, limit: Limit = 50
) -> MemoryPageOut:
    """The learner's live memories, newest first (superseded and deleted ones are not listed)."""
    page = await steward.list_memories(user, cursor=_str(cursor), limit=limit)
    return MemoryPageOut(
        items=[MemoryOut.model_validate(m) for m in page.items], next_cursor=page.next_cursor
    )


@router.patch("/memory/{memory_id}", responses=UNAVAILABLE)
async def pin_memory(memory_id: uuid.UUID, body: MemoryPatch, steward: Steward) -> MemoryOut:
    """`pinned`: always apply this memory (it is in every answer's context)."""
    pinned = await steward.set_memory_pinned(
        str(memory_id), body.pinned, idempotency_key=memory.key("pin")
    )
    return MemoryOut.model_validate(pinned)


@router.delete("/memory/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, responses=UNAVAILABLE)
async def delete_memory(memory_id: uuid.UUID, steward: Steward) -> Response:
    await memory.delete(steward, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/memory/candidates", responses=UNAVAILABLE)
async def list_candidates(
    steward: Steward, user: User, cursor: uuid.UUID | None = None, limit: Limit = 50
) -> CandidatePageOut:
    """Knowledge base cards steward suggests and asks the learner about."""
    page = await steward.list_candidates(user, cursor=_str(cursor), limit=limit)
    return CandidatePageOut(
        items=[CandidateOut.model_validate(c) for c in page.items], next_cursor=page.next_cursor
    )


@router.post(
    "/memory/candidates/{candidate_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={**UNAVAILABLE, 409: {"description": "the card is decided already"}},
)
async def decide_candidate(
    candidate_id: uuid.UUID, body: CandidateDecision, steward: Steward, user: User
) -> Response:
    """accept: the card becomes a knowledge base entry (in steward's inbox collection)."""
    await steward.decide_candidate(
        str(candidate_id), accept=body.decision == "accept", user_id=user, note=body.note,
        idempotency_key=memory.key("decide"),
    )  # fmt: skip
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _str(cursor: uuid.UUID | None) -> str | None:
    return str(cursor) if cursor is not None else None
