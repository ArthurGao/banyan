"""Feedback and re-answering (design §6.3), deprecating on node delete (§4), usage (§11.2)."""

import json
import uuid
from typing import Any

import httpx

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import Job, JobKind, Message, MessageRole, MessageStatus, Source, UsageLog
from tests.conftest import (
    NOT_FRESH,
    Sessions,
    create_node,
    create_topic,
    rows,
    run_next,
    sse_events,
    worker_ctx,
)

KB_CITATION = {"kind": "kb", "entry_id": "e1", "item_id": "e1", "retrieval_id": "r1"}


async def asked(api: httpx.AsyncClient, fake_llm: FakeLLM) -> tuple[str, str, str]:
    """A branch with one question answered by compose (source llm)."""
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "ClusterIP is the default.")
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Services")
    branch = (await api.post(f"/v1/nodes/{node['id']}/branches", json={"title": "Q"})).json()
    await api.post(f"/v1/branches/{branch['id']}/ask", json={"question": "Default type?"})
    latest = (await api.get(f"/v1/branches/{branch['id']}/messages/latest")).json()
    return node["id"], branch["id"], latest["id"]


async def kb_answer(db: Sessions, branch_id: str) -> uuid.UUID:
    async with db() as s:
        m = Message(
            branch_id=uuid.UUID(branch_id),
            role=MessageRole.ASSISTANT,
            status=MessageStatus.DONE,
            source=Source.KB,
            content_md="From the KB [S1].",
            citations=[KB_CITATION],
        )
        s.add(m)
        await s.commit()
        return m.id


def kinds(jobs: list[Job]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(
        (j.kind.value, j.payload)
        for j in jobs
        if j.kind != "collection_ensure" and j.kind != "kb_propose"
    )


async def test_good_on_a_kb_answer_is_helpful_feedback_and_a_notes_update(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    node_id, branch_id, _ = await asked(api, fake_llm)
    answer = await kb_answer(db, branch_id)
    response = await api.post(f"/v1/messages/{answer}/feedback", json={"feedback": "good"})
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["feedback"] == "good"
    assert kinds(await rows(db, Job)) == [
        ("kb_feedback", {"message_id": str(answer), "helpful": True, "note": None}),
        ("notes_update", {"node_id": node_id}),
    ]


async def test_wrong_without_reanswer_deprecates_what_the_answer_proposed(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    _, _, answer = await asked(api, fake_llm)
    async with db() as s:  # kb_propose has written its entry
        proposed = await s.get(Message, uuid.UUID(answer))
        assert proposed is not None
        proposed.proposed_entry_ids = ["e9"]
        await s.commit()
    response = await api.post(
        f"/v1/messages/{answer}/feedback",
        json={"feedback": "wrong", "note": "it is NodePort", "reanswer": False},
    )
    assert (response.json()["feedback"], response.json()["superseded_by"]) == ("wrong", None)
    # No KB items were cited, so no feedback to steward; its own proposal is deprecated.
    assert kinds(await rows(db, Job)) == [("kb_deprecate", {"message_id": answer})]
    # The same verdict again is merged, not queued twice.
    await api.post(f"/v1/messages/{answer}/feedback", json={"feedback": "wrong", "reanswer": False})
    assert len([j for j in await rows(db, Job) if j.kind == JobKind.KB_DEPRECATE]) == 1


async def test_wrong_answers_again_and_supersedes_the_old_answer(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    _, branch_id, old = await asked(api, fake_llm)
    fake_llm.script(Role.ANSWER_COMPOSE, "NodePort is not the default; ClusterIP is.")
    response = await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "incomplete"})
    events = sse_events(response.text)
    assert events[-1][0] == "done"
    new = events[-1][1]["message_id"]
    path = {p["step"]: p for p in events[-1][1]["path"]}
    assert path["freshness"]["detail"] == {"reason": "force_llm"}
    assert path["judge"]["detail"] == {"reason": "force_llm"}
    rerun = fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt
    assert "Question: Default type?" in rerun and "ClusterIP is the default." not in rerun
    messages = (await api.get(f"/v1/branches/{branch_id}")).json()["messages"]
    by_id = {m["id"]: m for m in messages}
    assert by_id[old]["superseded_by"] == new and by_id[old]["feedback"] == "incomplete"
    again = await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "wrong"})
    assert again.status_code == 409


async def test_a_wrong_explanation_is_replaced_by_its_reanswer(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, "Old explanation.", "New explanation.")
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Services")
    await api.post(f"/v1/nodes/{node['id']}/explain")
    old = (await api.get(f"/v1/nodes/{node['id']}")).json()["explanation"]["id"]
    events = sse_events(
        (await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "wrong"})).text
    )
    assert events[-1][0] == "done"
    current = (await api.get(f"/v1/nodes/{node['id']}")).json()["explanation"]
    assert (current["id"], current["content_md"]) == (
        events[-1][1]["message_id"],
        "New explanation.",
    )


async def test_a_good_answer_in_an_open_branch_reaches_the_notes(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    node_id, _, answer = await asked(api, fake_llm)
    await api.post(f"/v1/messages/{answer}/feedback", json={"feedback": "good"})
    notes_json = json.dumps(
        {
            "summary": "s",
            "key_points": ["ClusterIP"],
            "examples": [],
            "pitfalls": [],
            "branch_highlights": [],
        }
    )
    writer = FakeLLM({Role.NOTES_UPDATE: [notes_json]})
    assert (
        await run_next(worker_ctx(db, migrated_url, writer), [JobKind.NOTES_UPDATE.value]) == "done"
    )
    assert "ClusterIP is the default." in writer.calls_of(Role.NOTES_UPDATE)[0].prompt
    assert node_id


async def test_feedback_on_a_question_or_an_unknown_message_is_404(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    _, branch_id, _ = await asked(api, fake_llm)
    question = next(m for m in await rows(db, Message) if m.role == MessageRole.USER)
    for target in (question.id, uuid.uuid4()):
        response = await api.post(f"/v1/messages/{target}/feedback", json={"feedback": "good"})
        assert response.status_code == 404
    assert branch_id


async def test_deleting_a_node_can_deprecate_its_entries_too(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    node_id, _, answer = await asked(api, fake_llm)
    async with db() as s:
        m = await s.get(Message, uuid.UUID(answer))
        assert m is not None
        m.proposed_entry_ids = ["e1", "e2"]
        await s.commit()
    deleted = await api.delete(f"/v1/nodes/{node_id}?deprecate_entries=true")
    assert deleted.json() == {"proposed_entry_ids": ["e1", "e2"]}
    assert kinds(await rows(db, Job)) == [("kb_deprecate", {"entry_ids": ["e1", "e2"]})]


async def test_deleting_a_node_keeps_its_entries_by_default(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    node_id, _, answer = await asked(api, fake_llm)
    async with db() as s:
        m = await s.get(Message, uuid.UUID(answer))
        assert m is not None
        m.proposed_entry_ids = ["e1"]
        await s.commit()
    deleted = await api.delete(f"/v1/nodes/{node_id}")
    assert deleted.json() == {"proposed_entry_ids": ["e1"]}
    assert kinds(await rows(db, Job)) == []  # kept in steward unless the learner opts in


async def test_usage_shows_the_hit_rate_and_the_estimated_saving(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    _, branch_id, answer = await asked(api, fake_llm)  # one compose answer (llm)
    await kb_answer(db, branch_id)  # and one from the KB
    topic_id = str(next(u.topic_id for u in await rows(db, UsageLog)))
    usage = (await api.get(f"/v1/usage?topic_id={topic_id}")).json()
    (one,) = usage
    compose = [u for u in await rows(db, UsageLog) if u.role == "answer.compose"]
    compose_tokens = sum((u.input_tokens or 0) + (u.output_tokens or 0) for u in compose)
    assert (one["answers"], one["kb_answers"], one["hit_rate"]) == (2, 1, 0.5)
    assert one["tokens_by_role"]["answer.compose"] == compose_tokens
    assert one["estimated_tokens_saved"] == compose_tokens  # 1 KB answer x the average compose
    assert answer


async def test_a_superseded_answer_is_left_out_of_the_next_questions_history(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    _, branch_id, old = await asked(api, fake_llm)
    fake_llm.script(Role.ANSWER_COMPOSE, "Corrected answer.")
    await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "wrong"})
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "Next answer.")
    await api.post(f"/v1/branches/{branch_id}/ask", json={"question": "And then?"})
    history = fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt
    assert "Corrected answer." in history
    assert "ClusterIP is the default." not in history  # the wrong answer is not fed back


async def test_a_superseded_answer_does_not_reach_the_notes(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, branch_id, old = await asked(api, fake_llm)
    fake_llm.script(Role.ANSWER_COMPOSE, "Corrected answer.")
    await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "wrong"})
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    notes_json = json.dumps(
        {"summary": "s", "key_points": [], "examples": [], "pitfalls": [], "branch_highlights": []}
    )
    writer = FakeLLM({Role.NOTES_UPDATE: [notes_json]})
    await run_next(worker_ctx(db, migrated_url, writer), [JobKind.NOTES_UPDATE.value])
    material = writer.calls_of(Role.NOTES_UPDATE)[0].prompt
    assert "Corrected answer." in material and "ClusterIP is the default." not in material


async def test_a_refused_reanswer_does_not_record_the_feedback(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    """The re-answer's checks run before anything is committed (a 409 changes nothing)."""
    _, branch_id, old = await asked(api, fake_llm)
    async with db() as s:  # another answer is streaming on the branch
        s.add(
            Message(
                branch_id=uuid.UUID(branch_id),
                role=MessageRole.ASSISTANT,
                status=MessageStatus.STREAMING,
            )
        )
        await s.commit()
    refused = await api.post(f"/v1/messages/{old}/feedback", json={"feedback": "wrong"})
    assert refused.status_code == 409
    stored = next(m for m in await rows(db, Message) if str(m.id) == old)
    assert stored.feedback == "none"
    assert kinds(await rows(db, Job)) == []


async def test_the_reanswer_is_told_the_verdict_and_the_note(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    # Owner decision 2026-11-13: the learner's note goes to the model that answers again (§6.3).
    _, _, old = await asked(api, fake_llm)
    first = fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt
    assert "marked an earlier answer" not in first
    fake_llm.script(Role.ANSWER_COMPOSE, "ClusterIP is the default; NodePort builds on it.")
    note = "It did not say how NodePort relates to it."
    response = await api.post(
        f"/v1/messages/{old}/feedback", json={"feedback": "incomplete", "note": note}
    )
    assert sse_events(response.text)[-1][0] == "done"
    rerun = fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt
    assert "marked an earlier answer to this as incomplete" in rerun
    assert note in rerun
    assert "ClusterIP is the default." not in rerun  # the old answer itself stays out


async def test_a_wrong_explanation_reanswer_is_told_the_note(
    api: httpx.AsyncClient, fake_llm: FakeLLM
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, "Old explanation.", "New explanation.")
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Services")
    await api.post(f"/v1/nodes/{node['id']}/explain")
    old = (await api.get(f"/v1/nodes/{node['id']}")).json()["explanation"]["id"]
    await api.post(
        f"/v1/messages/{old}/feedback", json={"feedback": "wrong", "note": "Ports are mixed up."}
    )
    rerun = fake_llm.calls_of(Role.NODE_EXPLAIN)[-1].prompt
    assert "marked an earlier answer to this as wrong, with this note: Ports are mixed up." in rerun
