"""The memory page (design §8.5): the learner's memories and KB candidate cards, managed in
steward directly. Synchronous by owner decision (2026-11-12, AGENTS.md rule 6): these change only
steward's state, and the learner expects to see the result. Each write has a fresh idempotency key:
there is no retry to replay, and a reused key would replay an earlier pin state. Steward's errors
become problems in api/errors.py (503 unreachable, its 404 / 409 as such)."""

import uuid

from banyan.errors import StewardNotConfigured
from banyan.steward import StewardClient, StewardRejected


def require(steward: StewardClient | None) -> StewardClient:
    if steward is None:
        raise StewardNotConfigured("steward is not configured (BANYAN_STEWARD_URL)")
    return steward


async def delete(steward: StewardClient, memory_id: uuid.UUID) -> None:
    try:
        await steward.delete_memory(str(memory_id), idempotency_key=key("delete"))
    except StewardRejected as exc:
        if exc.status != 409:
            raise  # 409: deleted already, which is what the learner asked for


def key(action: str) -> str:
    """A fresh idempotency key per write (see the module's docstring)."""
    return f"memory_page:{action}:{uuid.uuid4()}"
