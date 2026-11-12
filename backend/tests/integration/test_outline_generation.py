"""Outline generation over SSE (design §5.1 step 3, §9): `outline` then `done`, usage rows, and the
409 refusals before the stream opens."""

import asyncio
import contextlib
import json
import uuid
from typing import Any

import httpx

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import UsageLog
from banyan.pipeline.events import ErrorEvent, Event
from banyan.services import generation
from tests.conftest import Sessions, create_topic, rows, sse_events

OUTLINE = {
    "nodes": [
        {"title": "Pods", "summary": "The smallest unit.", "children": [
            {"title": "Pod networking", "summary": "One IP per pod.", "children": [
                {"title": "Pause container", "summary": "Holds the namespace."}]}]},
        {"title": "Services", "summary": "Stable addresses.", "children": []},
    ]
}  # fmt: skip


async def _topic(api: httpx.AsyncClient) -> str:
    topic_id: str = (await create_topic(api, title="Kubernetes networking", goal="pass the CKA"))[
        "id"
    ]
    return topic_id


async def _usage(db: Sessions) -> list[tuple[str, int, bool, str | None]]:
    return [(r.role, r.attempt, r.ok, r.error_kind) for r in await rows(db, UsageLog)]


async def test_the_outline_streams_then_is_saved_as_the_draft(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.OUTLINE_GENERATE, json.dumps(OUTLINE))
    topic_id = await _topic(api)

    response = await api.post(f"/v1/topics/{topic_id}/outline")
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    assert [name for name, _ in events] == ["path", "outline", "done"]
    nodes: list[dict[str, Any]] = events[1][1]["nodes"]
    assert [(n["title"], n["depth"], n["position"]) for n in nodes] == [
        ("Pods", 0, 0), ("Pod networking", 1, 0), ("Pause container", 2, 0), ("Services", 0, 1),
    ]  # fmt: skip
    done = events[2][1]
    assert done["message_id"] is None and done["usage"]["output"] > 0

    saved = (await api.get(f"/v1/topics/{topic_id}")).json()
    assert {n["title"] for n in saved["nodes"]} == {n["title"] for n in nodes}
    assert saved["topic"]["outline_status"] == "draft"
    assert await _usage(db) == [("outline.generate", 1, True, None)]
    (call,) = fake_llm.calls_of(Role.OUTLINE_GENERATE)
    assert "Kubernetes networking" in call.prompt and "pass the CKA" in call.prompt


async def test_regenerating_needs_replace_and_replaces(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    second = {"nodes": [{"title": "Only node", "summary": "s", "children": []}]}
    fake_llm.script(Role.OUTLINE_GENERATE, json.dumps(OUTLINE), json.dumps(second))
    topic_id = await _topic(api)
    await api.post(f"/v1/topics/{topic_id}/outline")

    refused = await api.post(f"/v1/topics/{topic_id}/outline")
    assert refused.status_code == 409
    replaced = sse_events((await api.post(f"/v1/topics/{topic_id}/outline?replace=true")).text)
    assert [n["title"] for n in replaced[1][1]["nodes"]] == ["Only node"]
    assert [n["title"] for n in (await api.get(f"/v1/topics/{topic_id}")).json()["nodes"]] == [
        "Only node"
    ]


async def test_a_confirmed_outline_cannot_be_regenerated(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.OUTLINE_GENERATE, json.dumps(OUTLINE))
    topic_id = await _topic(api)
    await api.post(f"/v1/topics/{topic_id}/outline")
    await api.post(f"/v1/topics/{topic_id}/outline/confirm", json={})
    assert (await api.post(f"/v1/topics/{topic_id}/outline?replace=true")).status_code == 409


async def test_a_failed_role_streams_an_error_records_usage_and_keeps_the_outline(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.OUTLINE_GENERATE, "not json", '{"nodes": []}')
    topic_id = await _topic(api)
    events = sse_events((await api.post(f"/v1/topics/{topic_id}/outline")).text)
    assert [name for name, _ in events] == ["path", "error"]
    assert events[1][1]["code"] == "role_validation"
    assert (await api.get(f"/v1/topics/{topic_id}")).json()["nodes"] == []
    assert await _usage(db) == [
        ("outline.generate", 1, False, "validation"),
        ("outline.generate", 2, False, "validation"),
    ]


async def test_sse_events_carry_increasing_ids(api: httpx.AsyncClient, fake_llm: FakeLLM) -> None:
    fake_llm.script(Role.OUTLINE_GENERATE, json.dumps(OUTLINE))
    topic_id = await _topic(api)
    body = (await api.post(f"/v1/topics/{topic_id}/outline")).text
    ids = [line.split(": ", 1)[1] for line in body.splitlines() if line.startswith("id: ")]
    assert ids == ["1", "2", "3"]


async def test_confirming_during_a_regeneration_keeps_the_confirmed_outline(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    """The 'not confirmed' check before the stream is not enough: confirmation can land while the
    model is still answering, and the generated outline must then not replace it."""
    fake_llm.script(Role.OUTLINE_GENERATE, json.dumps(OUTLINE))
    topic_id = await _topic(api)
    await api.post(f"/v1/topics/{topic_id}/outline")
    slow = FakeLLM({Role.OUTLINE_GENERATE: [json.dumps({"nodes": [
        {"title": "Replacement", "summary": "s", "children": []}]})]}, delay_s=0.3)  # fmt: skip

    async def regenerate() -> list[Event]:
        stream = generation.generate_outline(db, slow.client(), uuid.UUID(topic_id))
        return [e async for e in stream]

    running = asyncio.create_task(regenerate())
    await asyncio.sleep(0.1)  # the model is answering
    confirmed = await api.post(f"/v1/topics/{topic_id}/outline/confirm", json={})
    assert confirmed.status_code == 200
    events = await running

    assert isinstance(events[-1], ErrorEvent) and events[-1].code == "conflict"
    titles = {n["title"] for n in (await api.get(f"/v1/topics/{topic_id}")).json()["nodes"]}
    assert "Replacement" not in titles and "Pods" in titles
    assert [u[0] for u in await _usage(db)] == ["outline.generate", "outline.generate"]


async def test_a_client_leaving_mid_generation_still_records_usage(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    topic_id = await _topic(api)
    slow = FakeLLM({Role.OUTLINE_GENERATE: [json.dumps(OUTLINE)]}, delay_s=0.5)

    async def consume() -> None:
        async for _ in generation.generate_outline(db, slow.client(), uuid.UUID(topic_id)):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    task.cancel()  # what the SSE response does when the client disconnects
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert await _usage(db) == [("outline.generate", 1, False, "aborted")]
    assert (await api.get(f"/v1/topics/{topic_id}")).json()["nodes"] == []
