"""A question branch goes to steward as an episode (design §8.2, §8.5): the turns after the
branch's watermark plus the learning state, once the branch is closed or holds enough new turns.
Run by the worker (AGENTS.md rule 6); the key comes from the last message sent, so a retry
replays steward's first answer."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from banyan.models import Branch, BranchKind, MessageRole
from banyan.services import branches, learning_state
from banyan.steward import EpisodeMessage, StewardClient

MAX_MESSAGES = 100  # steward's EpisodeCreate limit, the learning state included
MAX_CHARS = 20_000  # per message


async def submit(
    sessions: async_sessionmaker[AsyncSession], steward: StewardClient, branch_id: uuid.UUID,
    *, user_id: str, every: int,
) -> bool:  # fmt: skip
    """True when an episode went to steward."""
    async with sessions() as s:
        branch = await s.get(Branch, branch_id)
        if branch is None or branch.kind != BranchKind.QUESTION:
            return False
        new = await branches.unsent(s, branch)
        asked = sum(1 for m in new if m.role == MessageRole.USER)
        if not new or (branch.closed_at is None and asked < every):
            return False
        state = await learning_state.describe(s, branch.node_id)
        watermark, last = branch.episode_through, new[-1].id
    messages = [
        EpisodeMessage(m.role.value, m.content_md[:MAX_CHARS])
        for m in new[-(MAX_MESSAGES - 1) :]
        if m.content_md
    ]
    await steward.submit_episode(
        user_id=user_id,
        messages=[*messages, EpisodeMessage("system", state)],
        idempotency_key=f"episode:{branch_id}:{last}",
    )
    async with sessions() as s:
        await branches.advance_mark(s, branch_id, "episode_through", read=watermark, to=last)
    return True
