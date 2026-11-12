"""`web_fetch`: one page through the outbound guard, its main text as Markdown, cut to a budget
(design §7.3). Which URLs may be fetched at all (the allowlist) is the answer loop's decision."""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx
import trafilatura

from banyan.tools import outbound

READABLE = ("text/html", "application/xhtml+xml", "text/plain")
USER_AGENT = "Banyan/1 (personal learning assistant)"


@dataclass(frozen=True)
class FetchedPage:
    url: str
    title: str
    text_md: str
    fetched_at: datetime


class FetchFailed(Exception):
    """The page could not be read (refused URL, too large, HTTP error, no readable text). Handed
    to the model as the tool's result."""


class Fetcher:
    def __init__(
        self,
        *,
        max_bytes: int,
        max_chars: int,
        allow_private: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        resolve: outbound.Resolver = outbound.system_resolve,
    ) -> None:
        self.max_bytes, self.max_chars, self.allow_private = max_bytes, max_chars, allow_private
        self.transport, self.resolve = transport, resolve

    async def fetch(self, url: str) -> FetchedPage:
        try:
            async with outbound.client(self.transport) as http:
                got = await outbound.get(
                    http, url, max_bytes=self.max_bytes, headers={"User-Agent": USER_AGENT},
                    allow_private=self.allow_private, resolve=self.resolve,
                )  # fmt: skip
        except outbound.UrlNotAllowed as exc:
            raise FetchFailed(f"refused: {exc}") from exc
        except outbound.TooLarge as exc:
            raise FetchFailed(str(exc)) from exc
        except httpx.HTTPError as exc:
            raise FetchFailed(f"unreachable: {type(exc).__name__}") from exc
        if got.status_code != 200:
            raise FetchFailed(f"HTTP {got.status_code}")
        kind = got.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if kind not in READABLE:
            raise FetchFailed(f"unsupported content type {kind or 'unknown'}")
        text = got.body.decode(errors="replace")
        if kind != "text/plain":
            title, text = await asyncio.to_thread(_readable, text, got.url)
        else:
            title = ""
        if not text.strip():
            raise FetchFailed("no readable text")
        if len(text) > self.max_chars:
            text = text[: self.max_chars] + "\n\n[truncated]"
        return FetchedPage(got.url, title, text, datetime.now(UTC))


def _readable(html: str, url: str) -> tuple[str, str]:
    """Main text as Markdown, without navigation, footers and comments; and the page title."""
    text = trafilatura.extract(
        html, url=url, output_format="markdown", include_comments=False, include_tables=True,
        include_formatting=True,
    )  # fmt: skip
    meta = trafilatura.extract_metadata(html)
    return (meta.title or "") if meta else "", text or ""


@dataclass
class FakeFetcher:
    """Tests and BANYAN_FAKES=1: canned pages by URL; any other URL fails like a dead link."""

    pages: dict[str, FetchedPage] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)

    async def fetch(self, url: str) -> FetchedPage:
        self.urls.append(url)
        if url not in self.pages:
            raise FetchFailed("HTTP 404")
        return self.pages[url]
