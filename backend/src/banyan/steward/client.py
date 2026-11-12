"""Banyan's view of llm-steward (design §8.3): the Protocol and the types it returns.

Episodes, memories and KB candidates (B5) follow design §8.2 and §8.5.
"""

from dataclasses import dataclass, field
from datetime import datetime
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


@dataclass(frozen=True)
class EpisodeMessage:
    role: Literal["user", "assistant", "system"]
    content: str


@dataclass(frozen=True)
class Memory:
    """A memory steward's engine extracted about the learner (design §8.5); `pinned` ones are in
    every retrieval (owner decision: "always apply")."""

    id: str
    content: str
    memory_type: str
    pinned: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Candidate:
    """A KB card steward suggests and asks the learner about (`awaiting_user`)."""

    id: str
    title: str
    content: str
    reason: str | None
    risk_level: Risk
    created_at: datetime
    expires_at: datetime | None


@dataclass(frozen=True)
class Page[T]:
    items: list[T]
    next_cursor: str | None


def collection_name(topic_id: object) -> str:
    """A topic's steward collection (design §8.2). Retrieval matches collections by NAME and
    ignores ids without an error (B0 spike), so every caller uses this one rule."""
    return f"topic-{topic_id}"


class StewardReader(Protocol):
    """What requests use: health and retrieval. Fakes implement this much."""

    async def health(self) -> StewardHealth: ...

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


class StewardClient(StewardReader, Protocol):
    """Everything Banyan does with steward. Writes tied to Banyan's own state run in the worker
    (AGENTS.md rule 6); the learner's management of memories and candidates is synchronous."""

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

    async def submit_episode(
        self, *, user_id: str, messages: list[EpisodeMessage], idempotency_key: str
    ) -> str: ...

    async def list_memories(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Memory]: ...

    async def set_memory_pinned(
        self, memory_id: str, pinned: bool, *, idempotency_key: str
    ) -> Memory: ...

    async def delete_memory(self, memory_id: str, *, idempotency_key: str) -> None: ...

    async def list_candidates(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Candidate]: ...

    async def decide_candidate(
        self, candidate_id: str, *, accept: bool, user_id: str, note: str | None = None,
        idempotency_key: str,
    ) -> None: ...  # fmt: skip
