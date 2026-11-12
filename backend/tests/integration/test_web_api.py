"""Web answers over the API (design §9): `tool` and `citation` events, the stored citation, and
`allow_web=false`."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM, ToolCall
from banyan.models import Job, JobKind, Message, MessageRole, MessageStatus, Source
from banyan.steward import FakeSteward
from banyan.tools.fetch import FakeFetcher
from banyan.tools.search import FakeSearch, SearchResult
from banyan.tools.web import Web
from tests.conftest import (
    API_TOKEN,
    FRESH,
    Sessions,
    client_for,
    create_branch,
    create_node,
    create_topic,
    rows,
    sse_events,
)
from tests.pipeline.helpers import entry

URL = "https://kubernetes.io/releases/"


@pytest.fixture
async def web_api(migrated_url: str, fake_llm: FakeLLM) -> AsyncIterator[httpx.AsyncClient]:
    web = Web(FakeSearch([SearchResult(URL, "Releases", "1.40 is out")]), FakeFetcher())
    async with client_for(migrated_url, fake_llm, token=API_TOKEN, web=web) as client:
        yield client


async def branch(api: httpx.AsyncClient) -> str:
    topic = await create_topic(api)
    return await create_branch(api, (await create_node(api, topic["id"], "Releases"))["id"])


async def test_a_web_answer_streams_its_tool_calls_and_citations(
    web_api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, FRESH)
    fake_llm.script(
        Role.ANSWER_COMPOSE, ToolCall("web_search", {"query": "kubernetes latest"}), "1.40 [W1]."
    )
    body = (
        await web_api.post(
            f"/v1/branches/{await branch(web_api)}/ask", json={"question": "Latest?"}
        )
    ).text
    events = sse_events(body)
    assert ("tool", {"name": "web_search", "query": "kubernetes latest", "url": None}) in events
    (citation,) = [d for n, d in events if n == "citation"]
    assert (citation["kind"], citation["url"], citation["title"]) == ("web", URL, "Releases")
    assert events[-1][0] == "done" and events[-1][1]["source"] == "web"
    (answer,) = [m for m in await rows(db, Message) if m.role == MessageRole.ASSISTANT]
    assert answer.source == Source.WEB
    (stored,) = answer.citations
    assert (stored["kind"], stored["url"]) == ("web", URL)
    assert datetime.fromisoformat(stored["fetched_at"]).tzinfo is not None  # stored as JSON


async def test_allow_web_false_answers_without_web_tools_and_warns(
    web_api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "Probably 1.39.")
    url = f"/v1/branches/{await branch(web_api)}/ask"
    events = sse_events(
        (await web_api.post(url, json={"question": "Latest?", "allow_web": False})).text
    )
    assert events[-1][1]["warnings"] == ["stale_risk"]
    assert fake_llm.calls_of(Role.ANSWER_COMPOSE)[0].tools == ["kb_search"]


async def test_an_entry_from_an_expired_web_answer_is_dropped_and_deprecated(
    migrated_url: str, fake_llm: FakeLLM, db: Sessions
) -> None:
    steward = FakeSteward(items=[entry("old"), entry("fresh")])
    async with client_for(migrated_url, fake_llm, token=API_TOKEN, steward=steward) as api:
        branch_id = await branch(api)
        now = datetime.now(UTC)
        async with db() as s:  # two earlier web answers whose entries steward still returns
            for entry_id, valid_to in (
                ("old", now - timedelta(days=1)),
                ("fresh", now + timedelta(days=1)),
            ):
                s.add(
                    Message(
                        branch_id=uuid.UUID(branch_id),
                        role=MessageRole.ASSISTANT,
                        status=MessageStatus.DONE,
                        source=Source.WEB,
                        content_md="x",
                        proposed_entry_ids=[entry_id],
                        valid_to=valid_to,
                    )
                )
            await s.commit()
        fake_llm.script(Role.JUDGE_FRESHNESS, '{"needs_web": false}')
        fake_llm.script(Role.JUDGE_SUFFICIENCY, '{"sufficient": false, "coverage": 0.2}')
        fake_llm.script(Role.ANSWER_COMPOSE, "An answer.")
        await api.post(f"/v1/branches/{branch_id}/ask", json={"question": "Q?"})
    assert "tfresh" in fake_llm.calls_of(Role.JUDGE_SUFFICIENCY)[0].prompt
    assert "told" not in fake_llm.calls_of(Role.JUDGE_SUFFICIENCY)[0].prompt
    jobs = [j for j in await rows(db, Job) if j.kind == JobKind.KB_DEPRECATE]
    assert [(j.idempotency_key, j.payload) for j in jobs] == [
        ("kb_deprecate:expired:old", {"entry_ids": ["old"]})
    ]


async def test_a_web_answer_is_stored_with_its_expiry(
    web_api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, ToolCall("web_search", {"query": "q"}), "1.40 [W1].")
    before = datetime.now(UTC)
    await web_api.post(f"/v1/branches/{await branch(web_api)}/ask", json={"question": "Latest?"})
    (answer,) = [m for m in await rows(db, Message) if m.role == MessageRole.ASSISTANT]
    assert answer.valid_to is not None
    assert before + timedelta(days=90) <= answer.valid_to <= datetime.now(UTC) + timedelta(days=90)
