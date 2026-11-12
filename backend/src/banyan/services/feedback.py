"""Feedback on an answer (design §6.3): it reaches steward for the knowledge the answer cited, a
wrong answer's proposed entries are deprecated, a good one goes into the notes. All of it through
jobs in the same transaction as the feedback (AGENTS.md rule 6); re-answering is answers.py's."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from banyan.models import Branch, Feedback, JobKind, Message, enqueue
from banyan.services import branches
from banyan.services.lookup import get_or_404


async def record_feedback(
    s: AsyncSession, message_id: uuid.UUID, feedback: Feedback, note: str | None
) -> Message:
    """Stores the verdict and enqueues its jobs, uncommitted: the caller commits, with the
    re-answer's placeholder when it answers again, so a refused re-answer records nothing."""
    message = await branches.get_answer(s, message_id)
    branch = await get_or_404(s, Branch, message.branch_id)
    message.feedback, message.feedback_note = feedback, note
    # One job per (answer, verdict): the same verdict given twice is merged, not counted twice.
    if any(c.get("kind") == "kb" for c in message.citations):
        await enqueue(
            s, JobKind.KB_FEEDBACK, key=f"kb_feedback:{message_id}:{feedback.value}",
            payload={"message_id": str(message_id), "helpful": feedback == Feedback.GOOD,
                     "note": note},
        )  # fmt: skip
    # Nothing proposed yet: kb_propose sees the verdict and does not publish (proposals.propose).
    if feedback == Feedback.WRONG and message.proposed_entry_ids:
        await enqueue(
            s, JobKind.KB_DEPRECATE, key=f"kb_deprecate:{message_id}",
            payload={"message_id": str(message_id)},
        )  # fmt: skip
    if feedback == Feedback.GOOD:
        await enqueue(
            s, JobKind.NOTES_UPDATE, key=f"notes_update:good:{message_id}",
            payload={"node_id": str(branch.node_id)}, dedupe_key=f"notes:{branch.node_id}",
        )  # fmt: skip
    return message
