"""Search (Tavily's wire format on a fake network) and page fetching (design §7.3)."""

import json

import httpx
import pytest
from pydantic import SecretStr

from banyan.tools.fetch import Fetcher, FetchFailed
from banyan.tools.search import SearchFailed, SearchResult
from banyan.tools.search.tavily import TavilySearch
from tests.unit.fake_net import PUBLIC, resolve, streamed

ARTICLE = (
    "<html><head><title>Kubernetes 1.40 | Blog</title></head><body><nav>Home Docs Blog</nav>"
    "<article><h1>Kubernetes 1.40</h1><p>"
    + "Kubernetes 1.40 makes sidecar containers generally available. " * 6
    + "</p></article><footer>Copyright</footer></body></html>"
)


def tavily(handler: object) -> TavilySearch:
    return TavilySearch(SecretStr("tvly-test"), transport=httpx.MockTransport(handler))  # type: ignore[arg-type]


async def test_tavily_sends_the_documented_request_and_maps_the_results() -> None:
    sent: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append(req)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"url": "https://k8s.io/r", "title": "Release", "content": "1.40 is out",
                     "score": 0.9, "published_date": "2026-11-01"},
                    {"url": "https://k8s.io/b", "title": "Blog", "content": "news", "score": 0.5},
                ]
            },
        )  # fmt: skip

    found = await tavily(handler).search("latest kubernetes", max_results=5, recency_days=30)
    (req,) = sent
    assert (req.method, str(req.url)) == ("POST", "https://api.tavily.com/search")
    assert req.headers["authorization"] == "Bearer tvly-test"
    assert json.loads(req.content) == {
        "query": "latest kubernetes", "max_results": 5, "search_depth": "basic",
        "topic": "general", "time_range": "month",
    }  # fmt: skip
    assert found == [
        SearchResult("https://k8s.io/r", "Release", "1.40 is out", "2026-11-01"),
        SearchResult("https://k8s.io/b", "Blog", "news", None),
    ]


@pytest.mark.parametrize("status", [401, 429, 432, 500])
async def test_a_tavily_error_is_a_failed_search_without_the_body(status: int) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="key tvly-test is invalid")

    with pytest.raises(SearchFailed) as caught:
        await tavily(handler).search("q", max_results=3)
    assert str(caught.value) == f"search failed with HTTP {status}"


async def test_an_unreachable_search_is_a_failed_search() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(SearchFailed):
        await tavily(handler).search("q", max_results=3)


def fetcher(routes: dict[str, httpx.Response], *, max_chars: int = 10_000) -> Fetcher:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == PUBLIC  # always through the guard
        return streamed(routes[req.headers["host"] + req.url.path])

    return Fetcher(
        max_bytes=100_000, max_chars=max_chars, transport=httpx.MockTransport(handler),
        resolve=resolve,
    )  # fmt: skip


def html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"content-type": "text/html; charset=utf-8"})


async def test_a_page_becomes_its_main_text_as_markdown() -> None:
    page = await fetcher({"docs.example.com/k8s": html(ARTICLE)}).fetch(
        "https://docs.example.com/k8s"
    )
    assert page.title == "Kubernetes 1.40"
    assert page.text_md.startswith("# Kubernetes 1.40")
    assert "Home Docs Blog" not in page.text_md and "Copyright" not in page.text_md
    assert page.url == "https://docs.example.com/k8s"


async def test_a_long_page_is_cut_to_the_budget() -> None:
    page = await fetcher({"docs.example.com/k8s": html(ARTICLE)}, max_chars=50).fetch(
        "https://docs.example.com/k8s"
    )
    assert page.text_md.endswith("\n\n[truncated]")
    assert len(page.text_md) == 50 + len("\n\n[truncated]")


@pytest.mark.parametrize(
    ("url", "response", "reason"),
    [
        ("https://169.254.169.254/latest", None, "refused"),
        ("https://docs.example.com/gone", httpx.Response(404), "HTTP 404"),
        (
            "https://docs.example.com/a.pdf",
            httpx.Response(200, content=b"%PDF", headers={"content-type": "application/pdf"}),
            "unsupported content type application/pdf",
        ),
        ("https://docs.example.com/empty", html("<html><body></body></html>"), "no readable text"),
    ],
)
async def test_what_cannot_be_read_is_a_failed_fetch(
    url: str, response: httpx.Response | None, reason: str
) -> None:
    routes = {} if response is None else {"docs.example.com" + url.split(".com", 1)[1]: response}
    with pytest.raises(FetchFailed) as caught:
        await fetcher(routes).fetch(url)
    assert str(caught.value).startswith(reason)
