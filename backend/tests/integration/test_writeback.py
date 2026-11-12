"""Answers back to the KB (design §6.2 step 6, §6.3, §8.2): kb_propose, kb_feedback and
kb_deprecate through the worker, on FakeSteward."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import (
    Job,
    JobKind,
    JobStatus,
    Message,
    MessageRole,
    MessageStatus,
    Source,
    UsageLog,
    enqueue,
)
from banyan.services import proposals
from banyan.steward import FakeSteward, StewardRejected
from banyan.worker.context import WorkerContext
from tests.conftest import Sessions, explained_node, rows, run_next, worker_ctx

EXPLANATION = "A Service gives pods one stable virtual IP."


def draft(risk: str = "low") -> str:
    return json.dumps(
        {"title": "What a Service does", "content": "A stable IP for pods.", "risk_level": risk}
    )


async def explained(api: httpx.AsyncClient, fake_llm: FakeLLM) -> tuple[str, str, str]:
    return await explained_node(api, fake_llm, EXPLANATION)


async def run_kind(ctx: WorkerContext, kind: JobKind) -> str | None:
    return await run_next(ctx, [kind.value])


async def job_of(db: Sessions, kind: JobKind) -> Job:
    return next(j for j in await rows(db, Job) if j.kind == kind)


async def test_an_answer_is_proposed_with_the_design_tags(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    topic_id, node_id, message_id = await explained(api, fake_llm)
    steward = FakeSteward()
    writer = FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]})
    ctx = worker_ctx(db, migrated_url, writer, steward)

    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "pending"  # the collection is not there yet
    assert (await job_of(db, JobKind.KB_PROPOSE)).attempts == 0
    assert await run_kind(ctx, JobKind.COLLECTION_ENSURE) == "done"
    async with db() as s:  # skip the wait
        await s.execute(Job.__table__.update().values(run_after=Job.created_at))
        await s.commit()
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "done"

    ((entry_id, entry),) = steward.entries.items()
    assert entry.tags == [
        "app:banyan",
        f"topic:{topic_id}",
        f"node:{node_id}",
        "source:llm",
        "author:ai",
        f"origin_message:{message_id}",
    ]
    assert (
        entry.content.startswith("A stable IP for pods.")
        and "Source: a Banyan answer" in entry.content
    )
    (call,) = writer.calls_of(Role.KB_DRAFT_ENTRY)
    assert EXPLANATION in call.prompt and 'Explain "Services"' in call.prompt
    message = next(m for m in await rows(db, Message) if str(m.id) == message_id)
    assert message.proposed_entry_ids == [entry_id]
    usage = [u for u in await rows(db, UsageLog) if u.role == "kb.draft_entry"]
    assert [(str(u.message_id), u.path_kind) for u in usage] == [(message_id, "kb_propose")]


async def test_a_high_risk_draft_is_held_by_steward_not_proposed(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, _, message_id = await explained(api, fake_llm)
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, FakeLLM({Role.KB_DRAFT_ENTRY: [draft("high")]}), steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "done"
    assert steward.entries == {}
    message = next(m for m in await rows(db, Message) if str(m.id) == message_id)
    assert message.proposed_entry_ids == []


async def test_a_rejected_proposal_dies_at_once(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    await explained(api, fake_llm)
    steward = FakeSteward()

    async def reject(*_: Any, **__: Any) -> Any:
        raise StewardRejected(422, "content too long")

    steward.create_entry = reject  # type: ignore[method-assign]
    ctx = worker_ctx(db, migrated_url, FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]}), steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "dead"
    assert [u.ok for u in await rows(db, UsageLog) if u.role == "kb.draft_entry"] == [True]


async def test_feedback_goes_to_each_cited_kb_item_and_a_retry_sends_nothing_twice(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    _, node_id, _ = await explained(api, fake_llm)
    branch = (await api.post(f"/v1/nodes/{node_id}/branches", json={"title": "Q"})).json()
    async with db() as s:
        answer = Message(
            branch_id=uuid.UUID(branch["id"]),
            role=MessageRole.ASSISTANT,
            status=MessageStatus.DONE,
            content_md="From the KB [S1][S2].",
            citations=[
                {"kind": "kb", "entry_id": "e1", "item_id": "e1", "retrieval_id": "r1"},
                {"kind": "kb", "entry_id": "e2", "item_id": "e2", "retrieval_id": "r2"},
                {"kind": "web", "url": "https://example.com"},
            ],
        )
        s.add(answer)
        await s.commit()
    steward = FakeSteward()
    for _ in range(2):  # the job ran, then ran again (a retry after a lost lease)
        sent = await proposals.give_feedback(
            db, steward, answer.id, helpful=False, note="wrong", idempotency_key="kb_feedback:a"
        )
        assert sent == 2
    assert sorted(steward.feedback_given) == [("r1", "e1", False), ("r2", "e2", False)]


async def test_wrong_on_a_proposed_answer_deprecates_its_entries(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, _, message_id = await explained(api, fake_llm)
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]}), steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    await run_kind(ctx, JobKind.KB_PROPOSE)
    await api.post(
        f"/v1/messages/{message_id}/feedback", json={"feedback": "wrong", "reanswer": False}
    )
    assert await run_kind(ctx, JobKind.KB_DEPRECATE) == "done"
    assert [e.status for e in steward.entries.values()] == ["deprecated"]


async def test_deprecating_given_entries_skips_ones_steward_lost(
    db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    calls: list[str] = []

    async def deprecate(entry_id: str, *, idempotency_key: str) -> None:
        calls.append(entry_id)
        if entry_id == "gone":
            raise StewardRejected(404, "no such entry")

    steward.deprecate_entry = deprecate  # type: ignore[method-assign]
    async with db() as s:
        await enqueue(
            s,
            JobKind.KB_DEPRECATE,
            key="kb_deprecate:node:x",
            payload={"entry_ids": ["gone", "kept"]},
        )
        await s.commit()
    assert (
        await run_kind(worker_ctx(db, migrated_url, steward=steward), JobKind.KB_DEPRECATE)
        == "done"
    )
    assert calls == ["gone", "kept"]
    assert (await job_of(db, JobKind.KB_DEPRECATE)).status == JobStatus.DONE


async def test_an_answer_marked_wrong_before_its_proposal_is_never_published(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, _, message_id = await explained(api, fake_llm)
    await api.post(
        f"/v1/messages/{message_id}/feedback", json={"feedback": "wrong", "reanswer": False}
    )
    steward = FakeSteward()
    writer = FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]})
    ctx = worker_ctx(db, migrated_url, writer, steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "done"
    assert steward.entries == {} and writer.calls_of(Role.KB_DRAFT_ENTRY) == []  # no draft paid
    assert [j for j in await rows(db, Job) if j.kind == JobKind.KB_DEPRECATE] == []


async def test_an_answer_marked_wrong_while_it_was_proposed_is_deprecated_by_the_proposal(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, _, message_id = await explained(api, fake_llm)
    steward = FakeSteward()

    writer = FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]})
    ctx = worker_ctx(db, migrated_url, writer, steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    original = steward.create_entry

    async def create_then_wrong(*args: Any, **kwargs: Any) -> Any:
        created = await original(*args, **kwargs)
        await api.post(
            f"/v1/messages/{message_id}/feedback", json={"feedback": "wrong", "reanswer": False}
        )
        return created

    steward.create_entry = create_then_wrong  # type: ignore[method-assign]
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "done"
    assert [e.status for e in steward.entries.values()] == ["deprecated"]


async def test_a_web_answer_is_proposed_with_its_urls_and_expiry(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    _, _, message_id = await explained(api, fake_llm)
    async with db() as s:  # make the explanation a web answer citing two pages
        message = await s.get(Message, uuid.UUID(message_id))
        assert message is not None
        message.source = Source.WEB
        message.citations = [
            {"kind": "web", "url": "https://a.example/x", "title": "A",
             "fetched_at": "2026-11-10T09:00:00+00:00"},
            {"kind": "web", "url": "https://b.example/y", "title": "B",
             "fetched_at": "2026-11-11T09:00:00+00:00"},
        ]  # fmt: skip
        message.valid_to = datetime(2027, 2, 8, 9, tzinfo=UTC)
        await s.commit()
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, FakeLLM({Role.KB_DRAFT_ENTRY: [draft()]}), steward)
    await run_kind(ctx, JobKind.COLLECTION_ENSURE)
    assert await run_kind(ctx, JobKind.KB_PROPOSE) == "done"
    (entry,) = steward.entries.values()
    assert entry.tags[3:4] == ["source:web"]
    assert entry.tags[-4:] == [
        "url:https://a.example/x",
        "url:https://b.example/y",
        "fetched_at:2026-11-10",
        "valid_to:2027-02-08",
    ]
    assert "https://a.example/x" in entry.content and "valid until 2027-02-08" in entry.content
