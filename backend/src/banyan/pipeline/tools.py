"""The answer loop's sources and tools (design §6.2 step 5, §7.3), built per answer.

`SourceBook` numbers everything the answer may cite: knowledge-base items `S1…` (retrieved, or
found by `kb_search`), memories `M1…` (context, never cited) and web pages `W1…` (the topic's
research, search results, fetched pages). Code maps the `[S#]` / `[W#]` an answer writes back to
citations and decides the answer's source from them.

The tools are plain async functions; the role layer hands them to the model. A tool that cannot do
its work says so in its result, which the model reads; that is not a role failure. `web_fetch`
only reads URLs from this answer's searches, the question and the topic's research (design §7.3).
"""

import re
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

from banyan.llm import Tool
from banyan.models import Source
from banyan.pipeline.sources import SourceText, Wrapper, kb_text, web_text
from banyan.pipeline.types import (
    AskInput,
    Citation,
    Deps,
    RetrievedSource,
    RetrieveOut,
    WebSource,
)
from banyan.steward import StewardUnavailable
from banyan.tools.fetch import FetchFailed
from banyan.tools.search import SearchFailed

CITED = re.compile(r"\[([SW]\d+)\]")
URL_IN_TEXT = re.compile(r"https?://[^\s<>()\[\]{}\"'`]+")
TRAILING = ".,;:!?"


def urls_in(text: str) -> list[str]:
    return [u.rstrip(TRAILING) for u in URL_IN_TEXT.findall(text)]


class SourceBook:
    def __init__(
        self, found: RetrieveOut | None = None, research: Sequence[WebSource] = ()
    ) -> None:
        self.kb: dict[str, RetrievedSource] = {}
        self.web: dict[str, WebSource] = {}
        self.memories = list(found.memories) if found else []
        self.retrieval_ids: list[str] = []  # kb_search's own retrievals
        self._kb_ids: dict[str, str] = {}
        self._urls: dict[str, str] = {}
        for item in found.entries if found else []:
            self.add_kb(item)
        for page in research:
            self.add_web(page)

    def add_kb(self, item: RetrievedSource) -> str:
        if item.id not in self._kb_ids:
            label = f"S{len(self.kb) + 1}"
            self.kb[label], self._kb_ids[item.id] = item, label
        return self._kb_ids[item.id]

    def add_web(self, page: WebSource) -> str:
        """A URL keeps its label; reading the page replaces the search snippet's title and time."""
        label = self._urls.get(page.url)
        if label is None:
            label = f"W{len(self.web) + 1}"
            self._urls[page.url] = label
            self.web[label] = page
        elif not page.summary:  # a fetched page: what was read, and when
            known = self.web[label]
            self.web[label] = page.model_copy(update={"title": page.title or known.title})
        return label

    def knows(self, url: str) -> bool:
        return url in self._urls

    def initial(self, wrapper: Wrapper) -> str:
        """The material the model starts from: retrieved knowledge, memories, the research."""
        material = [SourceText(label, "kb", kb_text(s)) for label, s in self.kb.items()]
        material += [
            SourceText(f"M{i}", "memory", m.content) for i, m in enumerate(self.memories, 1)
        ]
        material += [SourceText(label, "web", web_text(p)) for label, p in self.web.items()]
        return wrapper.wrap(material)

    def cited(self, text: str) -> list[str]:
        return [
            label
            for label in dict.fromkeys(CITED.findall(text))
            if label in self.kb or label in self.web
        ]

    def citations(self, text: str) -> list[Citation]:
        out: list[Citation] = []
        for label in self.cited(text):
            if label in self.kb:
                s = self.kb[label]
                out.append(
                    Citation(
                        kind="kb",
                        entry_id=s.id if s.kind == "entry" else None,
                        item_id=s.id,
                        retrieval_id=s.retrieval_id,
                        title=s.title,
                    )
                )
            else:
                p = self.web[label]
                out.append(Citation(kind="web", url=p.url, title=p.title, fetched_at=p.fetched_at))
        return out

    def source(self, text: str) -> Source:
        """Decided by code from what the answer cites (design §6.2): web only → web, web and the
        knowledge base → mixed, no web → llm."""
        cited = self.cited(text)
        web = any(label.startswith("W") for label in cited)
        kb = any(label.startswith("S") for label in cited)
        return (Source.MIXED if kb else Source.WEB) if web else Source.LLM


async def drop_expired(deps: Deps, items: list[RetrievedSource]) -> list[RetrievedSource]:
    """Leaves out entries from a Banyan web answer that has expired, and collects them for
    writeback to deprecate (design §8.2). The one place this rule lives: drop it once steward's
    EntryCreate takes valid_to."""
    if not items or deps.expired is None:
        return items
    gone = await deps.expired([i.id for i in items])
    deps.expired_seen |= gone
    return [i for i in items if i.id not in gone]


ToolLog = list[dict[str, str]]
Status = Literal["ok", "failed", "refused", "empty"]


def build_tools(
    inp: AskInput, deps: Deps, book: SourceBook, wrapper: Wrapper, log: ToolLog, *, web: bool
) -> list[Tool]:
    """`kb_search` when steward is configured; `web_search` and `web_fetch` when `web` is on."""
    asked = set(urls_in(inp.question))

    def note(name: str, status: Status, error: str | None = None, **arg: str) -> None:
        """Into the answer's path (design §6.2): each call, its outcome and, failed, why."""
        entry = {"name": name, **arg, "status": status}
        log.append(entry | {"error": error[:300]} if error else entry)

    async def kb_search(query: str) -> str:
        """Search the learner's knowledge base: what was learned and confirmed before, across
        topics. Results are labelled S1, S2, …; cite one as [S1] right after the sentence."""
        assert deps.steward is not None
        try:
            result = await deps.steward.retrieve(
                query, user_id=deps.user_id, collections=inp.all_collections,
                include_memory=False, top_k=deps.config.top_k,
                token_budget=deps.config.context_budget,
            )  # fmt: skip
        except StewardUnavailable:
            result = None
        if result is None or "kb" in result.degraded:
            note("kb_search", "failed", "the knowledge base is unavailable", query=query)
            return "The knowledge base is unavailable right now."
        book.retrieval_ids.append(result.retrieval_id)
        items = [RetrievedSource.from_item(i, result.retrieval_id) for i in result.items]
        found = await drop_expired(deps, [s for s in items if s.kind != "memory"])
        note("kb_search", "ok" if found else "empty", query=query)
        if not found:
            return "Nothing in the knowledge base matches."
        return wrapper.wrap(SourceText(book.add_kb(s), "kb", kb_text(s)) for s in found)

    async def web_search(query: str) -> str:
        """Search the web. Results are labelled W1, W2, … with their URL and a snippet; cite one as
        [W1] right after the sentence, and read one in full with web_fetch."""
        assert deps.web is not None
        try:
            results = await deps.web.search.search(query, max_results=deps.web.max_results)
        except SearchFailed as exc:
            note("web_search", "failed", str(exc), query=query)
            return f"Search failed: {exc}"
        now = datetime.now(UTC)
        pages = [WebSource.from_result(r, now) for r in results]
        note("web_search", "ok" if pages else "empty", query=query)
        if not pages:
            return "No results."
        return wrapper.wrap(SourceText(book.add_web(p), "web", web_text(p)) for p in pages)

    async def web_fetch(url: str) -> str:
        """Read one web page in full, as Markdown. Only URLs from web_search results, the question
        or the reference material can be read; cite the page by its label."""
        assert deps.web is not None
        url = url.strip()
        if not (book.knows(url) or url in asked):
            note("web_fetch", "refused", "not from a search, the question or the research", url=url)
            return (
                "Refused: only URLs from web_search results, the question or the reference "
                "material can be read."
            )
        try:
            page = await deps.web.fetcher.fetch(url)
        except FetchFailed as exc:
            note("web_fetch", "failed", str(exc), url=url)
            return f"Could not read {url}: {exc}"
        note("web_fetch", "ok", url=url)
        read = WebSource(url=url, title=page.title, fetched_at=page.fetched_at)
        label = book.add_web(read)
        return wrapper.wrap([SourceText(label, "web", web_text(book.web[label], page.text_md))])

    tools: list[Tool] = []
    if deps.steward is not None:
        tools.append(kb_search)
    if web:
        tools += [web_search, web_fetch]
    return tools
