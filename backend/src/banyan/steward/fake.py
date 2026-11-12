"""In-memory steward for tests (AGENTS.md rule 8). B1: health and retrieval; B3 adds the rest."""

import uuid

from banyan.steward.client import RetrievalItem, RetrievalResult, StewardHealth, StewardUnavailable


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
        if self.unavailable:
            raise StewardUnavailable("POST /v1/retrieve: ConnectError")
        return RetrievalResult(
            retrieval_id=str(uuid.uuid4()),
            items=[i for i in self.items if include_memory or i.kind != "memory"],
            context=None,
            degraded=list(self.degraded),
        )
