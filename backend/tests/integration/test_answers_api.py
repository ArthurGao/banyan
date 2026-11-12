"""Explain and ask over SSE (design §5.2, §9) on the real test database: the commit point writes
the answer, its usage and its jobs together; failures and aborts enqueue nothing."""

import asyncio
import contextlib
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from banyan.ids import uuid7
from banyan.llm import CallContext, Role, UsageCollector, UsageRecord
from banyan.llm.fakes import FakeLLM
from banyan.models import Job, JobKind, Message, MessageRole, MessageStatus, UsageLog
from banyan.pipeline import record
from banyan.services import answers
from banyan.steward import FakeSteward
from banyan.worker.sweep import sweep_stale_streams
from tests.conftest import Sessions, create_node, create_topic, rows, sse_events

EXPLANATION = "A Service gives a set of pods one stable virtual IP and DNS name."
NOT_FRESH = '{"needs_web": false}'


async def _node(api: httpx.AsyncClient) -> dict[str, Any]:
    topic = await create_topic(api)
    return await create_node(api, topic["id"], "Services", summary="Stable IPs")


async def _db(db: Sessions) -> dict[str, list[Any]]:
    return {
        "messages": await rows(db, Message),
        "usage": await rows(db, UsageLog),
        "jobs": [j for j in await rows(db, Job) if j.kind != "collection_ensure"],
    }


async def test_explaining_a_node_streams_then_commits_everything_together(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    node = await _node(api)

    events = sse_events((await api.post(f"/v1/nodes/{node['id']}/explain")).text)
    names = [n for n, _ in events]
    assert names[0] == "path" and names[-1] == "done" and "token" in names
    message_id = events[0][1]["detail"]["message_id"]
    assert "".join(d["text"] for n, d in events if n == "token") == EXPLANATION
    done = events[-1][1]
    assert (done["message_id"], done["source"], done["warnings"]) == (message_id, "llm", [])
    assert [p["step"] for p in done["path"]] == [
        "scope", "freshness", "retrieve", "judge", "answer", "writeback",
    ]  # fmt: skip

    db = await _db(db)
    (message,) = db["messages"]
    assert (str(message.id), message.status, message.content_md) == (
        message_id, MessageStatus.DONE, EXPLANATION,
    )  # fmt: skip
    assert message.provenance == (
        "judge.freshness:fake:fake-judge.freshness,node.explain:fake:fake-node.explain"
    )
    assert len(message.steward_retrieval_ids) == 3
    assert [(u.role, u.ok, str(u.message_id)) for u in db["usage"]] == [
        ("judge.freshness", True, message_id), ("node.explain", True, message_id),
    ]  # fmt: skip
    assert sorted(j.idempotency_key for j in db["jobs"]) == [
        f"kb_propose:{message_id}", f"notes_update:{message_id}",
    ]  # fmt: skip

    detail = (await api.get(f"/v1/nodes/{node['id']}")).json()
    assert detail["explanation"]["content_md"] == EXPLANATION
    assert detail["node"]["status"] == "learning"
    assert [b["kind"] for b in detail["branches"]] == ["main"]
    assert (await api.post(f"/v1/nodes/{node['id']}/explain")).status_code == 409


async def test_asking_in_a_branch_sends_the_history_and_the_explanation(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH, NOT_FRESH, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    fake_llm.script(Role.ANSWER_COMPOSE, "ClusterIP is the default type.", "Use NodePort.")
    node = await _node(api)
    await api.post(f"/v1/nodes/{node['id']}/explain")
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    url = f"/v1/branches/{branch['id']}/ask"

    first = sse_events((await api.post(url, json={"question": "Default type?"})).text)
    assert first[-1][0] == "done"
    await api.post(url, json={"question": "And from outside the cluster?"})

    second = fake_llm.calls_of(Role.ANSWER_COMPOSE)[1].prompt
    assert "Default type?" in second and "ClusterIP is the default type." in second
    assert EXPLANATION in second and "And from outside the cluster?" in second
    messages = (await api.get(f"/v1/branches/{branch['id']}")).json()["messages"]
    assert [(m["role"], m["content_md"]) for m in messages] == [
        ("user", "Default type?"), ("assistant", "ClusterIP is the default type."),
        ("user", "And from outside the cluster?"), ("assistant", "Use NodePort."),
    ]  # fmt: skip
    latest = (await api.get(f"/v1/branches/{branch['id']}/messages/latest")).json()
    assert (latest["content_md"], latest["status"]) == ("Use NodePort.", "done")


async def test_a_failed_role_marks_the_answer_failed_with_usage_and_no_jobs(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, ConnectionError("provider down"))
    node = await _node(api)
    events = sse_events((await api.post(f"/v1/nodes/{node['id']}/explain")).text)
    assert events[-1] == ("error", {"code": "role_provider", "message": events[-1][1]["message"]})

    db = await _db(db)
    (message,) = db["messages"]
    assert message.status == MessageStatus.FAILED
    assert [p["step"] for p in message.path][-1] == "answer"
    assert [(u.role, u.ok, u.error_kind) for u in db["usage"]] == [
        ("judge.freshness", True, None), ("node.explain", False, "provider"),
    ]  # fmt: skip
    assert db["jobs"] == []
    # A failed explanation can be retried: the node has no explanation yet.
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    retry = sse_events((await api.post(f"/v1/nodes/{node['id']}/explain")).text)
    assert retry[-1][0] == "done"


async def test_a_second_stream_on_a_branch_is_a_409(api: httpx.AsyncClient, db: Sessions) -> None:
    node = await _node(api)
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    async with db() as s:
        s.add(Message(branch_id=uuid.UUID(branch["id"]), role=MessageRole.ASSISTANT,
                      status=MessageStatus.STREAMING))  # fmt: skip
        await s.commit()
    response = await api.post(f"/v1/branches/{branch['id']}/ask", json={"question": "Q?"})
    assert response.status_code == 409
    # The refused question was not stored either (one transaction with the placeholder).
    assert (await api.get(f"/v1/branches/{branch['id']}")).json()["messages"] == []


async def test_a_closed_branch_takes_no_questions(api: httpx.AsyncClient) -> None:
    node = await _node(api)
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    await api.patch(f"/v1/branches/{branch['id']}", json={"closed": True})
    response = await api.post(f"/v1/branches/{branch['id']}/ask", json={"question": "Q?"})
    assert response.status_code == 409


RuntimeFor = Callable[[FakeLLM], answers.Runtime]


@pytest.fixture
def runtime_for(db: Sessions) -> RuntimeFor:
    """A Runtime on the test schema, to drive `stream_answer` directly as the SSE response does."""

    def build(fake: FakeLLM) -> answers.Runtime:
        return answers.Runtime(
            llm=fake.client(), steward=FakeSteward(), sessions=db, user_id="owner",
            history_budget=2000,
        )  # fmt: skip

    return build


async def test_a_client_leaving_mid_answer_aborts_it_with_usage_and_no_jobs(
    api: httpx.AsyncClient, db: Sessions, runtime_for: RuntimeFor
) -> None:
    node = await _node(api)
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.NODE_EXPLAIN: ["x" * 400]},
                   delay_s=0.02)  # fmt: skip
    rt = runtime_for(fake)
    async with rt.sessions() as s:
        inp = await answers.start_explain(s, uuid.UUID(node["id"]))
    stream = answers.stream_answer(inp, rt)
    async for event in stream:
        if type(event).__name__ == "TokenEvent":
            break
    await stream.aclose()  # what the SSE response does when the client disconnects

    db = await _db(db)
    (message,) = db["messages"]
    assert message.status == MessageStatus.ABORTED
    assert [(u.role, u.ok, u.error_kind) for u in db["usage"]] == [
        ("judge.freshness", True, None), ("node.explain", False, "aborted"),
    ]  # fmt: skip
    assert db["jobs"] == []
    latest = (await api.get(f"/v1/branches/{message.branch_id}/messages/latest")).json()
    assert latest["status"] == "aborted"


async def test_leaving_after_the_commit_point_keeps_the_answer(
    api: httpx.AsyncClient, db: Sessions, runtime_for: RuntimeFor
) -> None:
    node = await _node(api)
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.NODE_EXPLAIN: [EXPLANATION]})
    rt = runtime_for(fake)
    async with rt.sessions() as s:
        inp = await answers.start_explain(s, uuid.UUID(node["id"]))
    stream = answers.stream_answer(inp, rt)
    async for event in stream:
        if type(event).__name__ == "DoneEvent":
            break
    await stream.aclose()

    db = await _db(db)
    assert [m.status for m in db["messages"]] == [MessageStatus.DONE]
    assert len(db["usage"]) == 2 and len(db["jobs"]) == 2


async def test_leaving_while_the_answer_is_being_committed_keeps_it(
    api: httpx.AsyncClient, db: Sessions, runtime_for: RuntimeFor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """The commit point (design §9): once the commit has started, a disconnect lets it finish and
    the answer is not marked aborted or its usage written twice."""
    node = await _node(api)
    rt = runtime_for(FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.NODE_EXPLAIN: [EXPLANATION]}))
    committing = asyncio.Event()
    real_done = record.done

    async def slow_done(*args: Any) -> None:
        committing.set()
        await asyncio.sleep(0.2)
        await real_done(*args)

    monkeypatch.setattr(record, "done", slow_done)
    async with rt.sessions() as s:
        inp = await answers.start_explain(s, uuid.UUID(node["id"]))
    stream = answers.stream_answer(inp, rt)

    async def consume() -> None:
        async for _ in stream:
            pass

    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(committing.wait(), timeout=5)
    consumer.cancel()  # the client goes away mid-commit
    with contextlib.suppress(asyncio.CancelledError):
        await consumer

    db = await _db(db)
    assert [m.status for m in db["messages"]] == [MessageStatus.DONE]
    assert [(u.role, u.ok) for u in db["usage"]] == [
        ("judge.freshness", True),
        ("node.explain", True),
    ]
    assert len(db["jobs"]) == 2


async def test_ending_an_already_committed_answer_changes_nothing(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, runtime_for: RuntimeFor
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    node = await _node(api)
    await api.post(f"/v1/nodes/{node['id']}/explain")
    (message,) = (await _db(db))["messages"]

    late = UsageCollector()
    late.add(
        UsageRecord(
            role="node.explain",
            provider="fake",
            model="m",
            attempt=1,
            input_tokens=1,
            output_tokens=1,
            ok=False,
            error_kind="aborted",
            latency_ms=1,
            context=CallContext(message_id=message.id),
        )
    )
    rt = runtime_for(FakeLLM())
    await record.ended(rt.sessions, message.id, MessageStatus.ABORTED, late, [])

    db = await _db(db)
    assert [m.status for m in db["messages"]] == [MessageStatus.DONE]
    assert len(db["usage"]) == 2


async def test_latest_is_deterministic_when_timestamps_tie(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    node = await _node(api)
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    at = datetime.now(UTC)
    older, newer = sorted([uuid7(), uuid7()])  # UUIDv7: a later id sorts higher
    async with db() as s:
        for message_id, text in [(newer, "newer"), (older, "older")]:
            s.add(Message(id=message_id, branch_id=uuid.UUID(branch["id"]),
                          role=MessageRole.ASSISTANT, status=MessageStatus.DONE, content_md=text,
                          created_at=at))  # fmt: skip
        await s.commit()
    for _ in range(5):
        latest = (await api.get(f"/v1/branches/{branch['id']}/messages/latest")).json()
        assert latest["content_md"] == "newer"


async def test_an_answer_commits_even_when_a_notes_update_is_already_waiting(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    """The explanation's notes_update shares dedupe key notes:<node> with one already waiting (e.g.
    from a closed branch): the second is merged away, not an error that loses the answer."""
    node = await _node(api)
    async with db() as s:
        s.add(Job(kind=JobKind.NOTES_UPDATE, idempotency_key="notes_update:earlier",
                  payload={"node_id": node["id"]}, dedupe_key=f"notes:{node['id']}"))  # fmt: skip
        await s.commit()
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    events = sse_events((await api.post(f"/v1/nodes/{node['id']}/explain")).text)
    assert events[-1][0] == "done", events[-1]
    db_rows = await _db(db)
    assert [m.status for m in db_rows["messages"]] == [MessageStatus.DONE]
    assert sorted(j.kind for j in db_rows["jobs"]) == [JobKind.KB_PROPOSE, JobKind.NOTES_UPDATE]


async def test_a_long_answer_is_not_swept_as_stale_while_it_runs(
    api: httpx.AsyncClient, db: Sessions
) -> None:
    """The sweeper fails abandoned placeholders only: a running answer keeps its own fresh."""
    node = await _node(api)
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.NODE_EXPLAIN: ["x" * 120]},
                   delay_s=0.1)  # about a second of streaming  # fmt: skip
    rt = answers.Runtime(llm=fake.client(), steward=FakeSteward(), sessions=db, user_id="owner",
                         history_budget=2000, stale_s=0.3)  # fmt: skip
    async with rt.sessions() as s:
        inp = await answers.start_explain(s, uuid.UUID(node["id"]))

    async def sweep_often() -> None:
        while True:
            await sweep_stale_streams(db, older_than_s=0.3)
            await asyncio.sleep(0.05)

    sweeper = asyncio.create_task(sweep_often())
    try:
        events = [e async for e in answers.stream_answer(inp, rt)]
    finally:
        sweeper.cancel()
    assert type(events[-1]).__name__ == "DoneEvent"
    assert [m.status for m in (await _db(db))["messages"]] == [MessageStatus.DONE]
