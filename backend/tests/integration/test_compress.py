"""A long branch keeps its early turns as a summary (design §5.3, §6.5): `branch.compress` runs in
the worker after an answer whose history no longer fit; the next question sends the summary and
the recent turns. FakeLLM; the default history budget (2,000 estimated tokens)."""

import json
import uuid

import httpx

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import Branch, Job, JobKind, Message, MessageRole, MessageStatus, UsageLog
from banyan.worker.context import WorkerContext
from tests.conftest import (
    NOT_FRESH,
    Sessions,
    create_branch,
    create_node,
    create_topic,
    rows,
    run_next,
    worker_ctx,
)

LONG = 3000  # characters: about 750 estimated tokens, so three messages pass the 2,000 budget


async def long_branch(api: httpx.AsyncClient, db: Sessions, turns: int) -> str:
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Generics")
    branch_id = await create_branch(api, node["id"])
    async with db() as s:
        for n in range(turns):
            for role, text in ((MessageRole.USER, f"Q{n} "), (MessageRole.ASSISTANT, f"A{n} ")):
                body = text * (LONG // 4)
                s.add(
                    Message(branch_id=uuid.UUID(branch_id), role=role,
                            status=MessageStatus.DONE, content_md=body)
                )  # fmt: skip
                await s.flush()
        await s.commit()
    return branch_id


async def ask(api: httpx.AsyncClient, fake_llm: FakeLLM, branch_id: str, question: str) -> str:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.ANSWER_COMPOSE, "An answer.")
    await api.post(f"/v1/branches/{branch_id}/ask", json={"question": question})
    return fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt


async def compress_jobs(db: Sessions) -> list[Job]:
    return [j for j in await rows(db, Job) if j.kind == JobKind.BRANCH_COMPRESS]


async def run_compress(ctx: WorkerContext) -> str | None:
    return await run_next(ctx, [JobKind.BRANCH_COMPRESS.value])


async def test_an_answer_over_the_budget_queues_one_compression(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    branch_id = await long_branch(api, db, 3)
    prompt = await ask(api, fake_llm, branch_id, "Next?")
    assert "Q0 Q0" not in prompt and "A2 A2" in prompt  # trimmed, as before
    await ask(api, fake_llm, branch_id, "And then?")
    assert len(await compress_jobs(db)) == 1  # one per branch while one waits


async def test_the_summary_replaces_the_early_turns_in_the_next_question(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    branch_id = await long_branch(api, db, 3)
    await ask(api, fake_llm, branch_id, "Next?")
    writer = FakeLLM({Role.BRANCH_COMPRESS: [json.dumps({"summary": "We covered erasure."})]})
    assert await run_compress(worker_ctx(db, migrated_url, writer)) == "done"
    (call,) = writer.calls_of(Role.BRANCH_COMPRESS)
    assert "Q0 Q0" in call.prompt and "Next?" not in call.prompt  # early turns, not the newest
    (branch,) = [b for b in await rows(db, Branch) if str(b.id) == branch_id]
    assert branch.summary_md == "We covered erasure."
    folded = [m for m in await rows(db, Message) if m.id == branch.summary_through]
    assert folded and folded[0].role == MessageRole.ASSISTANT
    assert [(u.role, u.ok) for u in await rows(db, UsageLog) if u.role == "branch.compress"] == [
        ("branch.compress", True)
    ]
    prompt = await ask(api, fake_llm, branch_id, "Last one?")
    assert "We covered erasure." in prompt and "Q0 Q0" not in prompt
    assert "Next?" in prompt  # the recent turns follow the summary
    # With the summary, what came after it fits: nothing more to compress.
    assert [j.status for j in await compress_jobs(db)] == ["done"]


async def test_a_short_branch_is_not_compressed(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Generics")
    branch_id = await create_branch(api, node["id"])
    await ask(api, fake_llm, branch_id, "Short?")
    assert await compress_jobs(db) == []


async def test_a_failed_compression_is_retried_and_the_branch_still_answers(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    branch_id = await long_branch(api, db, 3)
    await ask(api, fake_llm, branch_id, "Next?")
    broken = FakeLLM({Role.BRANCH_COMPRESS: [ConnectionError("down")]})
    assert await run_compress(worker_ctx(db, migrated_url, broken)) == "pending"
    (branch,) = [b for b in await rows(db, Branch) if str(b.id) == branch_id]
    assert branch.summary_md is None
    assert "An answer." in (await api.get(f"/v1/branches/{branch_id}")).text
    assert [u.ok for u in await rows(db, UsageLog) if u.role == "branch.compress"] == [False]


async def test_an_answer_answered_again_is_not_summarized(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    branch_id = await long_branch(api, db, 3)
    first_answer = min(
        (m for m in await rows(db, Message) if m.role == MessageRole.ASSISTANT),
        key=lambda m: (m.created_at, m.id),
    )
    async with db() as s:
        replaced = await s.get(Message, first_answer.id)
        assert replaced is not None
        replacement = Message(branch_id=uuid.UUID(branch_id), role=MessageRole.ASSISTANT,
                              status=MessageStatus.DONE, content_md="Better A0.")  # fmt: skip
        s.add(replacement)
        await s.flush()
        replaced.superseded_by = replacement.id
        await s.commit()
    await ask(api, fake_llm, branch_id, "Next?")
    writer = FakeLLM({Role.BRANCH_COMPRESS: [json.dumps({"summary": "S."})]})
    await run_compress(worker_ctx(db, migrated_url, writer))
    (call,) = writer.calls_of(Role.BRANCH_COMPRESS)
    assert "A0 A0" not in call.prompt


async def test_answering_again_keeps_the_branch_summary(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    branch_id = await long_branch(api, db, 3)
    await ask(api, fake_llm, branch_id, "Next?")
    writer = FakeLLM({Role.BRANCH_COMPRESS: [json.dumps({"summary": "We covered erasure."})]})
    await run_compress(worker_ctx(db, migrated_url, writer))
    await ask(api, fake_llm, branch_id, "Last one?")
    last = max(
        (m for m in await rows(db, Message) if m.role == MessageRole.ASSISTANT),
        key=lambda m: (m.created_at, m.id),
    )
    fake_llm.script(Role.ANSWER_COMPOSE, "Better.")
    await api.post(f"/v1/messages/{last.id}/feedback", json={"feedback": "wrong"})
    prompt = fake_llm.calls_of(Role.ANSWER_COMPOSE)[-1].prompt
    assert "We covered erasure." in prompt and "Question: Last one?" in prompt
