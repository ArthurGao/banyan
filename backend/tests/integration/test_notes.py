"""Notes updates (design §5.4, §10.1) through the worker handler on the test database."""

import json
import uuid
from typing import Any

import httpx

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import Job, JobKind, JobStatus, NodeNotes, UsageLog
from banyan.services import notes
from banyan.worker.context import WorkerContext
from tests.conftest import Sessions, create_node, create_topic, rows, run_next, worker_ctx

EXPLANATION = "Services give pods a stable virtual IP."


def notes_json(**fields: Any) -> str:
    return json.dumps({"summary": "s", "key_points": [], "examples": [], "pitfalls": [],
                       "branch_highlights": [], **fields})  # fmt: skip


async def run_notes_job(c: WorkerContext) -> str | None:
    return await run_next(c, [JobKind.NOTES_UPDATE.value])


async def explained_node(api: httpx.AsyncClient, fake_llm: FakeLLM) -> str:
    fake_llm.script(Role.JUDGE_FRESHNESS, '{"needs_web": false}')
    fake_llm.script(Role.NODE_EXPLAIN, EXPLANATION)
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Services")
    await api.post(f"/v1/nodes/{node['id']}/explain")
    node_id: str = node["id"]
    return node_id


async def test_an_explanation_becomes_the_first_notes(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)
    writer = FakeLLM({Role.NOTES_UPDATE: [notes_json(key_points=["Stable virtual IP"])]})
    assert (await api.get(f"/v1/nodes/{node_id}/notes")).json()["version"] == 0

    assert await run_notes_job(worker_ctx(db, migrated_url, writer)) == "done"
    got = (await api.get(f"/v1/nodes/{node_id}/notes")).json()
    assert (got["version"], got["content"]["key_points"]) == (1, ["Stable virtual IP"])
    (call,) = writer.calls_of(Role.NOTES_UPDATE)
    assert EXPLANATION in call.prompt and 'kind="kb"' in call.prompt
    assert [u.role for u in await rows(db, UsageLog) if u.role == "notes.update"] == [
        "notes.update"
    ]


async def test_a_closed_branch_is_merged_in_once(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)
    writer = FakeLLM({Role.NOTES_UPDATE: [
        notes_json(key_points=["Stable virtual IP"]),
        notes_json(key_points=["NodePort exposes a port"],
                   branch_highlights=[{"branch_id": "x", "question": "Q", "answer_md": "A"}]),
    ]})  # fmt: skip
    c = worker_ctx(db, migrated_url, writer)
    await run_notes_job(c)
    fake_llm.script(Role.JUDGE_FRESHNESS, '{"needs_web": false}')
    fake_llm.script(Role.ANSWER_COMPOSE, "Use a NodePort.")
    branch = (await api.post(f"/v1/nodes/{node_id}/branches", json={"title": "Outside"})).json()
    await api.post(f"/v1/branches/{branch['id']}/ask", json={"question": "From outside?"})
    await api.patch(f"/v1/branches/{branch['id']}", json={"closed": True})

    assert await run_notes_job(c) == "done"
    got = (await api.get(f"/v1/nodes/{node_id}/notes")).json()
    assert got["version"] == 2
    assert got["content"]["key_points"] == ["Stable virtual IP", "NodePort exposes a port"]
    second = writer.calls_of(Role.NOTES_UPDATE)[1].prompt
    assert "From outside?" in second and "Use a NodePort." in second
    assert EXPLANATION not in second  # already incorporated
    (saved,) = await rows(db, NodeNotes)
    last_answer = (await api.get(f"/v1/branches/{branch['id']}")).json()["messages"][-1]["id"]
    assert saved.incorporated["branches"] == {branch["id"]: last_answer}
    # Nothing new: a further job changes nothing and calls no model.
    assert await notes.update_notes(db, writer.client(), uuid.UUID(node_id)) is False
    assert len(writer.calls_of(Role.NOTES_UPDATE)) == 2


async def test_closing_a_branch_queues_one_notes_job_per_node(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    node_id = await explained_node(api, fake_llm)  # the explanation already queued one
    for title in ("A", "B"):
        branch = (await api.post(f"/v1/nodes/{node_id}/branches", json={"title": title})).json()
        await api.patch(f"/v1/branches/{branch['id']}", json={"closed": True})
    waiting = [j for j in await rows(db, Job)
               if j.kind == JobKind.NOTES_UPDATE and j.status == JobStatus.PENDING]  # fmt: skip
    assert len(waiting) == 1  # merged by dedupe key notes:<node>


async def test_a_lost_version_race_waits_and_runs_again(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)

    def bump(_: str) -> str:
        return notes_json(key_points=["mine"])

    writer = FakeLLM({Role.NOTES_UPDATE: [bump]})
    c = worker_ctx(db, migrated_url, writer)
    async with db() as s:  # someone else saved version 1 after this job read "no notes"
        original = notes.get_notes
        calls = 0

        async def stale_read(session: Any, nid: uuid.UUID) -> NodeNotes | None:
            nonlocal calls
            calls += 1
            if calls == 1:
                s.add(NodeNotes(node_id=uuid.UUID(node_id), version=1, content={},
                                incorporated={}))  # fmt: skip
                await s.commit()
                return None
            return await original(session, nid)

        notes.get_notes = stale_read  # type: ignore[assignment]
        try:
            assert await run_notes_job(c) == "pending"
        finally:
            notes.get_notes = original  # type: ignore[assignment]
    (job,) = [j for j in await rows(db, Job) if j.kind == JobKind.NOTES_UPDATE]
    assert (job.status, job.attempts) == (JobStatus.PENDING, 0)


async def test_a_failed_notes_role_keeps_its_usage_and_retries(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)
    writer = FakeLLM({Role.NOTES_UPDATE: [ConnectionError("down")]})
    assert await run_notes_job(worker_ctx(db, migrated_url, writer)) == "pending"
    usage = [(u.role, u.ok) for u in await rows(db, UsageLog) if u.role == "notes.update"]
    assert usage == [("notes.update", False)]
    assert (await api.get(f"/v1/nodes/{node_id}/notes")).json()["version"] == 0


async def test_an_update_racing_another_update_does_not_overwrite_it(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)
    async with db() as s:  # notes at version 1 that have not taken in the explanation yet
        s.add(NodeNotes(node_id=uuid.UUID(node_id), version=1, content={}, incorporated={}))
        await s.commit()
    writer = FakeLLM({Role.NOTES_UPDATE: [notes_json(key_points=["mine"])]})
    original = notes.get_notes

    async def read_then_someone_saves(session: Any, nid: uuid.UUID) -> NodeNotes | None:
        found = await original(session, nid)
        async with db() as other:  # another update lands while this one calls the model
            await other.execute(
                NodeNotes.__table__.update().values(version=2, content={"summary": "theirs"})
            )
            await other.commit()
        return found

    notes.get_notes = read_then_someone_saves  # type: ignore[assignment]
    try:
        assert await run_notes_job(worker_ctx(db, migrated_url, writer)) == "pending"
    finally:
        notes.get_notes = original  # type: ignore[assignment]
    (saved,) = await rows(db, NodeNotes)
    assert (saved.version, saved.content) == (2, {"summary": "theirs"})


async def test_turns_added_after_reopening_a_branch_reach_the_notes(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id = await explained_node(api, fake_llm)
    writer = FakeLLM({Role.NOTES_UPDATE: [notes_json(), notes_json(), notes_json()]})
    c = worker_ctx(db, migrated_url, writer)
    await run_notes_job(c)  # the explanation
    branch = (await api.post(f"/v1/nodes/{node_id}/branches", json={"title": "Q"})).json()
    url = f"/v1/branches/{branch['id']}"
    for question, answer in (
        ("First question?", "First answer."),
        ("Later question?", "Later answer."),
    ):
        fake_llm.script(Role.JUDGE_FRESHNESS, '{"needs_web": false}')
        fake_llm.script(Role.ANSWER_COMPOSE, answer)
        await api.patch(url, json={"closed": False})
        await api.post(f"{url}/ask", json={"question": question})
        await api.patch(url, json={"closed": True})
        assert await run_notes_job(c) == "done"
    first, later = (call.prompt for call in writer.calls_of(Role.NOTES_UPDATE)[1:])
    assert "First answer." in first
    assert "Later answer." in later and "First answer." not in later
