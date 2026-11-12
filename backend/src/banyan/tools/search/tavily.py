"""Tavily (design §13 item 1): the only module that knows its wire format (backend AGENTS.md)."""

import httpx
from pydantic import BaseModel, SecretStr

from banyan.tools import outbound
from banyan.tools.search import SearchFailed, SearchResult

TAVILY_URL = "https://api.tavily.com/search"
TIMEOUT_S = 15.0


class _TavilyResult(BaseModel):
    url: str
    title: str = ""
    content: str = ""
    published_date: str | None = None


class _TavilyResponse(BaseModel):
    results: list[_TavilyResult] = []


def _time_range(days: int) -> str:
    for limit, name in ((1, "day"), (7, "week"), (31, "month")):
        if days <= limit:
            return name
    return "year"


class TavilySearch:
    """`POST https://api.tavily.com/search` with a Bearer key; `search_depth=basic` (one credit)."""

    def __init__(
        self, api_key: SecretStr, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._key = api_key
        self._transport = transport

    async def search(
        self, query: str, *, max_results: int, recency_days: int | None = None
    ) -> list[SearchResult]:
        body: dict[str, object] = {
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "topic": "general",
        }
        if recency_days is not None:
            body["time_range"] = _time_range(recency_days)
        headers = {"Authorization": f"Bearer {self._key.get_secret_value()}"}
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=TIMEOUT_S, verify=outbound.tls()
            ) as http:
                response = await http.post(TAVILY_URL, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise SearchFailed(f"search unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            # The body may echo the request; only the status is reported.
            raise SearchFailed(f"search failed with HTTP {response.status_code}")
        parsed = _TavilyResponse.model_validate_json(response.content)
        return [
            SearchResult(r.url, r.title, r.content, r.published_date)
            for r in parsed.results[:max_results]
        ]
