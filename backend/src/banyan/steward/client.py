"""Banyan's view of llm-steward (design §8.3): the Protocol and the types it returns.

B0 covers what the contract spike exercises; episodes, memories and candidates join in B3/B5.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

StewardHealth = Literal["ok", "degraded", "error"]
ItemKind = Literal["memory", "entry", "document"]
Risk = Literal["low", "medium", "high"]


class StewardUnavailable(Exception):
    """Steward could not answer: connection error, timeout or 5xx. Retrieval degrades on this
    (design §6.2); a worker job retries."""


class StewardRejected(Exception):
    """Steward answered with a 4xx: the request is wrong and a retry will not fix it."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"steward rejected the request ({status}): {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class RetrievalItem:
    id: str  # the `item_id` for feedback
    kind: ItemKind
    status: str
    score: float
    title: str | None
    content: str


@dataclass(frozen=True)
class RetrievalResult:
    retrieval_id: str
    items: list[RetrievalItem]
    context: str | None
    degraded: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProposedEntry:
    """A write lands as live knowledge (`entry_id`) or waits as a candidate (`candidate_id`):
    steward re-grades risk, and high risk always goes to its review queue (design §8.1)."""

    entry_id: str | None
    candidate_id: str | None


class StewardClient(Protocol):
    async def health(self) -> StewardHealth: ...

    async def ensure_collection(
        self, name: str, *, review_policy: dict[str, str], idempotency_key: str
    ) -> str: ...

    async def create_entry(
        self,
        collection_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        risk_level: Risk,
        idempotency_key: str,
    ) -> ProposedEntry: ...

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        # Collection NAMES: steward matches names here and silently ignores ids (B0 spike).
        collections: list[str] | None = None,
        tags: list[str] | None = None,
        include_memory: bool = True,
        top_k: int | None = None,
        token_budget: int | None = None,
        as_context: bool = False,
    ) -> RetrievalResult: ...

    async def feedback(
        self,
        retrieval_id: str,
        item_id: str,
        *,
        helpful: bool,
        note: str | None = None,
        idempotency_key: str,
    ) -> None: ...

    async def deprecate_entry(self, entry_id: str, *, idempotency_key: str) -> None: ...
