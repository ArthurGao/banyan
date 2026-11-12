"""In-memory steward for tests and `BANYAN_FAKES=1` (AGENTS.md rule 8): the StewardClient surface
with the rules Banyan relies on — collections matched by name, tags AND, only live entries
retrievable, high risk held as a candidate, writes replayed per Idempotency-Key. Entries are
retrievable at once (real steward syncs within seconds, design §8.4)."""

import uuid
from dataclasses import dataclass

from banyan.steward.client import (
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
        items = [i for i in self.items if include_memory or i.kind != "memory"] + stored
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

    def _check(self) -> None:
        if self.unavailable:
            raise StewardUnavailable("steward unreachable (fake)")
