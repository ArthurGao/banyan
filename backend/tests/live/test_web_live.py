"""Real services for B4's acceptance (opt-in; they cost money, so they never run by default).

-m search: one Tavily search (1 credit) with BANYAN_SEARCH_API_KEY, then reading real pages
           through the outbound guard.
-m llm:    the answer loop on each tool-capable model of BANYAN_ROLES_FILE (or the refs in
           BANYAN_LLM_TEST_MODELS, comma-separated), with a fake search and fetcher: every model
           must search, and cite the page it found as [W#] (design §12 B4: "all models behave the
           same"). Never point it at llm-gateway subscription profiles.
Without their configuration they fail rather than skip.
"""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import SecretStr
from pydantic_ai import models

from banyan.llm import RoleClient, load_roles
from banyan.llm.roles import Role
from banyan.models import Source
from banyan.pipeline import steps
from banyan.pipeline.events import Event
from banyan.pipeline.types import AskInput, Deps, FreshnessOut, JudgeOut, RetrieveOut
from banyan.tools.fetch import FakeFetcher, FetchedPage, Fetcher, FetchFailed
from banyan.tools.search import FakeSearch, SearchResult
from banyan.tools.search.tavily import TavilySearch
from banyan.tools.web import Web

URL = "https://example.org/kube-release-notes"
PAGE = FetchedPage(
    URL, "Kube release notes", "Kube 9.4 was released on 2026-11-01. It is the latest.",
    datetime(2026, 11, 2, tzinfo=UTC),
)  # fmt: skip


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} is not set; this opt-in test needs it")
    return value


@pytest.mark.search
async def test_tavily_finds_pages_that_the_guarded_fetcher_can_read() -> None:
    search = TavilySearch(SecretStr(_env("BANYAN_SEARCH_API_KEY")))
    results = await search.search("Python asyncio TaskGroup documentation", max_results=5)
    assert results and all(r.url.startswith("https://") and r.title for r in results)
    fetcher = Fetcher(max_bytes=2_000_000, max_chars=16_000)
    readable = []
    for r in results:
        try:
            readable.append(await fetcher.fetch(r.url))
        except FetchFailed:
            continue  # some sites refuse robots or serve PDFs; one readable page is enough
    assert readable and readable[0].text_md.strip()


async def _ignore(event: Event) -> None:
    return None


def _models() -> list[str]:
    config = load_roles(Path(_env("BANYAN_ROLES_FILE")))
    wanted = os.environ.get("BANYAN_LLM_TEST_MODELS")
    if wanted:
        return [ref.strip() for ref in wanted.split(",") if ref.strip()]
    return [m.ref for m in config.models if m.tools]


@pytest.fixture
def real_models() -> Iterator[None]:
    """tests/conftest.py refuses every real model request; this opt-in test is the one that may."""
    with models.override_allow_model_requests(True):
        yield


@pytest.mark.llm
@pytest.mark.usefixtures("real_models")
async def test_every_tool_model_searches_and_cites_the_web_the_same_way() -> None:
    config = load_roles(Path(_env("BANYAN_ROLES_FILE")))
    failures: dict[str, str] = {}
    for ref in _models():
        roles = {**config.roles, Role.ANSWER_COMPOSE: ref}
        client = RoleClient.from_config(config.model_copy(update={"roles": roles}), timeout_s=120)
        search = FakeSearch([SearchResult(URL, "Kube release notes", "Kube 9.4 is out.")])
        deps = Deps(
            llm=client, steward=None, emit=_ignore, user_id="owner",
            web=Web(search, FakeFetcher({URL: PAGE})),
        )  # fmt: skip
        inp = AskInput(
            topic_id=uuid.uuid4(), node_id=uuid.uuid4(), message_id=uuid.uuid4(),
            branch_id=uuid.uuid4(), mode="ask",
            question="What is the latest version of Kube (the fictional orchestrator)?",
            topic_title="Kube", goal=None, level="beginner", language_name="English",
            instructions=None, node_title="Releases", node_summary=None,
            collection="topic-x", all_collections=["topic-x"],
        )  # fmt: skip
        try:
            answered = await steps.answer(
                inp, FreshnessOut(needs_web=True), RetrieveOut(status="not_configured"),
                JudgeOut(hit=False, skipped_because="needs_web"), deps,
            )  # fmt: skip
        except Exception as exc:  # report every model, then fail once
            failures[ref] = f"{type(exc).__name__}: {exc}"
            continue
        if not search.queries:
            failures[ref] = "did not call web_search"
        elif answered.source not in (Source.WEB, Source.MIXED) or URL not in {
            c.url for c in answered.citations
        }:
            failures[ref] = f"did not cite the page: source={answered.source}"
    assert not failures, failures
