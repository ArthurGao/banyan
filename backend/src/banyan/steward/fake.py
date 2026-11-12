"""In-memory steward for tests and `BANYAN_FAKES=1` (AGENTS.md rule 8): the StewardClient surface
with the rules Banyan relies on — collections matched by name, tags AND, only live entries
retrievable, high risk held as a candidate, writes replayed per Idempotency-Key. Entries are
retrievable at once (real steward syncs within seconds, design §8.4). An episode's user lines
become memories, so `BANYAN_FAKES=1` can show the memory page (real extraction is steward's, with
its own model; tests of it run on embedded steward); live memories are retrieved, pinned first;
a memory deleted twice and a card decided twice are 409, an unknown id 404."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from banyan.steward.client import (
    Candidate,
    EpisodeMessage,
    Memory,
    Page,
    ProposedEntry,
    RetrievalItem,
    RetrievalResult,
    Risk,
    StewardHealth,
    StewardRejected,
    StewardUnavailable,
)


@dataclass
class FakeEntry:
    collection_id: str
    title: str
    content: str
    tags: list[str]
    status: str = "active"


@dataclass
class FakeMemory:
    content: str
    pinned: bool = False
    status: str = "active"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def view(self, memory_id: str) -> Memory:
        return Memory(
            memory_id, self.content, "fact", self.pinned, self.created_at, self.created_at
        )


class FakeSteward:
    def __init__(
        self,
        health: StewardHealth = "ok",
        *,
        items: list[RetrievalItem] | None = None,
        degraded: list[str] | None = None,
        unavailable: bool = False,
    ) -> None:
        self.health_status = health
        self.items = items or []
        self.degraded = degraded or []
        self.unavailable = unavailable
        self.retrievals: list[dict[str, object]] = []
        self.collections: dict[str, str] = {}  # name -> id
        self.entries: dict[str, FakeEntry] = {}
        self.feedback_given: list[tuple[str, str, bool]] = []
        self.episodes: list[list[EpisodeMessage]] = []
        self.memories: dict[str, FakeMemory] = {}
        self.candidates: dict[str, Candidate] = {}
        self.decisions: dict[str, bool] = {}  # candidate id -> accepted
        self._replies: dict[str, object] = {}  # Idempotency-Key -> first answer

    async def health(self) -> StewardHealth:
        return self.health_status

    async def retrieve(
        self,
        query: str,
        *,
        user_id: str,
        collections: list[str] | None = None,
        tags: list[str] | None = None,
        include_memory: bool = True,
        top_k: int | None = None,
        token_budget: int | None = None,
        as_context: bool = False,
    ) -> RetrievalResult:
        self.retrievals.append({"query": query, "collections": collections, "tags": tags})
        self._check()
        # Steward's rules (B0 spike): collections by NAME, tags all required, only live entries.
        names = self.collections
        wanted = None if collections is None else {names[c] for c in collections if c in names}
        stored = [
            RetrievalItem(
                id=entry_id,
                kind="entry",
                status="active",
                score=0.5,
                title=e.title,
                content=e.content,
            )
            for entry_id, e in self.entries.items()
            if e.status == "active"
            and (wanted is None or e.collection_id in wanted)
            and set(tags or []) <= set(e.tags)
        ]
        live = sorted(
            ((i, mem) for i, mem in self.memories.items() if mem.status == "active"),
            key=lambda pair: not pair[1].pinned,
        )
        remembered = [
            RetrievalItem(i, "memory", "active", 0.5, None, mem.content) for i, mem in live
        ]
        items = [i for i in self.items if include_memory or i.kind != "memory"] + stored
        items = (remembered if include_memory else []) + items
        return RetrievalResult(
            retrieval_id=str(uuid.uuid4()),
            items=items[: top_k or len(items)],
            context=None,
            degraded=list(self.degraded),
        )

    async def ensure_collection(
        self, name: str, *, review_policy: dict[str, str], idempotency_key: str
    ) -> str:
        self._check()
        return self.collections.setdefault(name, str(uuid.uuid4()))

    async def create_entry(
        self,
        collection_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        risk_level: Risk,
        idempotency_key: str,
    ) -> ProposedEntry:
        self._check()
        if idempotency_key not in self._replies:
            if risk_level == "high":  # waits for a reviewer (design §8.1): a candidate only
                self._replies[idempotency_key] = ProposedEntry(None, str(uuid.uuid4()))
            else:
                entry_id = str(uuid.uuid4())
                self.entries[entry_id] = FakeEntry(collection_id, title, content, tags)
                self._replies[idempotency_key] = ProposedEntry(entry_id, None)
        reply = self._replies[idempotency_key]
        assert isinstance(reply, ProposedEntry)
        return reply

    async def feedback(
        self, retrieval_id: str, item_id: str, *, helpful: bool, note: str | None = None,
        idempotency_key: str,
    ) -> None:  # fmt: skip
        self._check()
        if idempotency_key not in self._replies:  # a replayed key changes nothing
            self._replies[idempotency_key] = None
            self.feedback_given.append((retrieval_id, item_id, helpful))

    async def deprecate_entry(self, entry_id: str, *, idempotency_key: str) -> None:
        self._check()
        if entry_id not in self.entries:
            raise StewardRejected(404, f"no entry {entry_id}")
        self.entries[entry_id].status = "deprecated"

    async def submit_episode(
        self, *, user_id: str, messages: list[EpisodeMessage], idempotency_key: str
    ) -> str:
        self._check()
        if idempotency_key not in self._replies:
            self.episodes.append(list(messages))
            for message in messages:
                if message.role == "user":
                    self.memories[str(uuid.uuid4())] = FakeMemory(message.content)
            self._replies[idempotency_key] = str(uuid.uuid4())
        reply = self._replies[idempotency_key]
        assert isinstance(reply, str)
        return reply

    async def list_memories(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Memory]:
        self._check()
        live = [mem.view(i) for i, mem in self.memories.items() if mem.status == "active"]
        return _page(live[::-1], cursor, limit)  # newest first

    async def set_memory_pinned(
        self, memory_id: str, pinned: bool, *, idempotency_key: str
    ) -> Memory:
        memory = self._memory(memory_id)
        memory.pinned = pinned
        return memory.view(memory_id)

    async def delete_memory(self, memory_id: str, *, idempotency_key: str) -> None:
        memory = self._memory(memory_id)
        if memory.status == "deleted":
            raise StewardRejected(409, "the memory is deleted already")
        memory.status = "deleted"

    async def list_candidates(
        self, user_id: str, *, cursor: str | None = None, limit: int = 50
    ) -> Page[Candidate]:
        self._check()
        pending = [c for i, c in self.candidates.items() if i not in self.decisions]
        return _page(pending, cursor, limit)

    async def decide_candidate(
        self, candidate_id: str, *, accept: bool, user_id: str, note: str | None = None,
        idempotency_key: str,
    ) -> None:  # fmt: skip
        self._check()
        if candidate_id not in self.candidates:
            raise StewardRejected(404, "no such candidate")
        if candidate_id in self.decisions:
            raise StewardRejected(409, "the candidate is decided already")
        self.decisions[candidate_id] = accept

    def _memory(self, memory_id: str) -> FakeMemory:
        self._check()
        if memory_id not in self.memories:
            raise StewardRejected(404, "no such memory")
        return self.memories[memory_id]

    def _check(self) -> None:
        if self.unavailable:
            raise StewardUnavailable("steward unreachable (fake)")


def _page[T: (Memory, Candidate)](items: list[T], cursor: str | None, limit: int) -> Page[T]:
    """Keyset paging like steward's: the cursor is the last id of the previous page."""
    start = next((n + 1 for n, item in enumerate(items) if item.id == cursor), 0) if cursor else 0
    chunk = items[start : start + limit]
    more = start + limit < len(items)
    return Page(chunk, chunk[-1].id if more and chunk else None)
