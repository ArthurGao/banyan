"""What the answer loop and outline research reach the web with (design §7.3)."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from banyan.tools.fetch import FakeFetcher, FetchedPage
from banyan.tools.search import FakeSearch, SearchProvider, SearchResult


class PageFetcher(Protocol):
    async def fetch(self, url: str) -> FetchedPage: ...


@dataclass(frozen=True)
class Web:
    search: SearchProvider
    fetcher: PageFetcher
    max_results: int = 5


DEMO_URL = "https://example.org/banyan-demo"


def demo_web() -> Web:
    """`BANYAN_FAKES=1`: one canned result and its page, so the frontend can show web answers."""
    result = SearchResult(DEMO_URL, "A fake web page", "Fake search result (BANYAN_FAKES=1).")
    page = FetchedPage(DEMO_URL, "A fake web page", "Fake page text (BANYAN_FAKES=1).",
                       datetime.now(UTC))  # fmt: skip
    return Web(FakeSearch([result]), FakeFetcher({DEMO_URL: page}))
