"""Web search behind one Protocol (design §7.3); the provider is chosen by configuration
(BANYAN_SEARCH_PROVIDER). Tavily is the default (owner decision 2026-11-12, design §13 item 1)."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    url: str
    title: str
    snippet: str
    published_at: str | None = None


class SearchFailed(Exception):
    """The search could not be done (network, provider error, quota). The answer loop hands this to
    the model as the tool's result; it is not a role failure."""


class SearchProvider(Protocol):
    async def search(
        self, query: str, *, max_results: int, recency_days: int | None = None
    ) -> list[SearchResult]: ...


@dataclass
class FakeSearch:
    """Tests and BANYAN_FAKES=1 (AGENTS.md rule 8): canned results, the queries it was asked."""

    results: list[SearchResult] = field(default_factory=list)
    fail: bool = False
    queries: list[str] = field(default_factory=list)

    async def search(
        self, query: str, *, max_results: int, recency_days: int | None = None
    ) -> list[SearchResult]:
        self.queries.append(query)
        if self.fail:
            raise SearchFailed("search failed (fake)")
        return self.results[:max_results]
