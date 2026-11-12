"""Question branches go to steward as episodes with the learning state (design §5.5, §8.2, §8.5):
when, what is sent, the watermark, and how failures end. FakeSteward; the real contract is in
tests/contract/test_memory_contract.py."""

import uuid

import httpx
import pytest

from banyan.llm import Role
from banyan.llm.fakes import FakeLLM
from banyan.models import Branch, Job, JobKind, JobStatus, Message
from banyan.steward import EpisodeMessage, FakeSteward, StewardRejected, StewardUnavailable
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


async def branch_with(api: httpx.AsyncClient, fake_llm: FakeLLM, turns: int) -> tuple[str, str]:
    """A question branch with `turns` answered questions: (node id, branch id)."""
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Generics")
    branch_id = await create_branch(api, node["id"])
    await ask(api, fake_llm, branch_id, turns)
    return node["id"], branch_id


async def ask(
    api: httpx.AsyncClient, fake_llm: FakeLLM, branch_id: str, turns: int, start: int = 0
) -> None:
    for n in range(start, start + turns):
        fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
        fake_llm.script(Role.ANSWER_COMPOSE, f"Answer {n}.")
        await api.post(f"/v1/branches/{branch_id}/ask", json={"question": f"Question {n}?"})


async def run_episodes(ctx: WorkerContext) -> list[str | None]:
    """Runs every pending episode job; their end states."""
    states: list[str | None] = []
    while (state := await run_next(ctx, [JobKind.EPISODE_SUBMIT.value])) is not None:
        states.append(state)
    return states


def texts(episode: list[EpisodeMessage]) -> list[tuple[str, str]]:
    return [(m.role, m.content) for m in episode if m.role != "system"]


async def test_a_branch_is_sent_once_it_has_enough_new_turns(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)  # every 5 turns, as the app counts
    _, branch_id = await branch_with(api, fake_llm, 4)
    # Not due yet: no job at all, rather than one per answer that finds nothing to do.
    assert [j for j in await rows(db, Job) if j.kind == JobKind.EPISODE_SUBMIT] == []
    await ask(api, fake_llm, branch_id, 1, start=4)
    assert await run_episodes(ctx) == ["done"]
    (episode,) = steward.episodes
    assert [c for r, c in texts(episode) if r == "user"] == [f"Question {n}?" for n in range(5)]
    assert texts(episode)[:2] == [("user", "Question 0?"), ("assistant", "Answer 0.")]
    assert episode[-1].role == "system" and "computed by Banyan" in episode[-1].content
    # The next one counts and starts after what was sent.
    await ask(api, fake_llm, branch_id, 4, start=5)
    assert await run_episodes(ctx) == []
    await ask(api, fake_llm, branch_id, 1, start=9)
    await run_episodes(ctx)
    assert [c for r, c in texts(steward.episodes[1]) if r == "user"] == [
        f"Question {n}?" for n in range(5, 10)
    ]


async def test_closing_a_branch_sends_what_is_left_and_only_once(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)  # default: every 5 turns
    _, branch_id = await branch_with(api, fake_llm, 1)
    await run_episodes(ctx)
    assert steward.episodes == []
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    (episode,) = steward.episodes
    assert texts(episode) == [("user", "Question 0?"), ("assistant", "Answer 0.")]
    (branch,) = [b for b in await rows(db, Branch) if str(b.id) == branch_id]
    last = max((m for m in await rows(db, Message) if m.branch_id == branch.id),
               key=lambda m: (m.created_at, m.id))  # fmt: skip
    assert branch.episode_through == last.id
    # Closed again later with nothing new: nothing is sent.
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": False})
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    assert len(steward.episodes) == 1


async def test_the_learning_state_is_computed_from_the_data(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)
    _, branch_id = await branch_with(api, fake_llm, 2)
    answers = [m for m in await rows(db, Message) if m.role == "assistant"]
    await api.post(
        f"/v1/messages/{answers[0].id}/feedback", json={"feedback": "wrong", "reanswer": False}
    )
    await api.post(
        f"/v1/messages/{answers[1].id}/feedback", json={"feedback": "incomplete", "reanswer": False}
    )
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    state = steward.episodes[0][-1].content
    assert "Node: Generics (not started)" in state
    assert "questions asked on this node: 2" in state
    assert "answers marked wrong: 1, incomplete: 1" in state
    assert "Topic progress: 0 done, 0 learning, 1 not started" in state
    assert "Weakest nodes: Generics (2 marks)" in state


async def test_an_answer_answered_again_is_not_sent(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)
    _, branch_id = await branch_with(api, fake_llm, 1)
    (answer,) = [m for m in await rows(db, Message) if m.role == "assistant"]
    fake_llm.script(Role.ANSWER_COMPOSE, "Better answer.")
    await api.post(f"/v1/messages/{answer.id}/feedback", json={"feedback": "wrong"})
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    assert texts(steward.episodes[0]) == [("user", "Question 0?"), ("assistant", "Better answer.")]


async def test_an_explanation_is_not_an_episode(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions
) -> None:
    fake_llm.script(Role.JUDGE_FRESHNESS, NOT_FRESH)
    fake_llm.script(Role.NODE_EXPLAIN, "An explanation.")
    topic = await create_topic(api)
    node = await create_node(api, topic["id"], "Generics")
    await api.post(f"/v1/nodes/{node['id']}/explain")
    assert [j for j in await rows(db, Job) if j.kind == JobKind.EPISODE_SUBMIT] == []


async def test_a_long_branch_is_cut_to_stewards_limits(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward, episode_every=100)
    _, branch_id = await branch_with(api, fake_llm, 0)
    async with db() as s:  # 60 turns, one answer far over steward's 20,000 characters
        for n in range(60):
            for role, text in (("user", f"Q{n}"), ("assistant", "x" * (25_000 if n == 59 else 5))):
                s.add(Message(branch_id=uuid.UUID(branch_id), role=role, status="done",
                              content_md=text))  # fmt: skip
                await s.flush()
        await s.commit()
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    (episode,) = steward.episodes
    assert len(episode) == 100  # the last 99 messages and the learning state
    assert episode[0].content == "x" * 5  # the oldest kept is an answer from turn 10
    assert len(episode[-2].content) == 20_000


@pytest.mark.parametrize(
    ("failure", "state"),
    [(StewardRejected(422, "bad episode"), "dead"), (StewardUnavailable("down"), "pending")],
)
async def test_a_refused_episode_dies_and_an_unreachable_steward_is_retried(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str,
    failure: Exception, state: str,
) -> None:  # fmt: skip
    steward = FakeSteward()

    async def fail(**kwargs: object) -> str:
        raise failure

    steward.submit_episode = fail  # type: ignore[method-assign]
    ctx = worker_ctx(db, migrated_url, steward=steward)
    _, branch_id = await branch_with(api, fake_llm, 1)
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    assert await run_next(ctx, [JobKind.EPISODE_SUBMIT.value]) == state
    (branch,) = [b for b in await rows(db, Branch) if str(b.id) == branch_id]
    assert branch.episode_through is None  # nothing sent, nothing marked
    if state == "dead":
        (job,) = [j for j in await rows(db, Job) if j.kind == JobKind.EPISODE_SUBMIT]
        assert job.status == JobStatus.DEAD


async def test_answering_the_last_sent_turn_again_does_not_resend_the_branch(
    api: httpx.AsyncClient, fake_llm: FakeLLM, db: Sessions, migrated_url: str
) -> None:
    steward = FakeSteward()
    ctx = worker_ctx(db, migrated_url, steward=steward)
    _, branch_id = await branch_with(api, fake_llm, 5)
    await run_episodes(ctx)  # the watermark is the 5th answer
    last = max(
        (m for m in await rows(db, Message) if m.role == "assistant"),
        key=lambda m: (m.created_at, m.id),
    )
    fake_llm.script(Role.ANSWER_COMPOSE, "Answer 4, corrected.")
    await api.post(f"/v1/messages/{last.id}/feedback", json={"feedback": "wrong"})
    await api.patch(f"/v1/branches/{branch_id}", json={"closed": True})
    await run_episodes(ctx)
    # Only what is new since the first episode: the corrected answer, not the whole branch.
    assert texts(steward.episodes[1]) == [("assistant", "Answer 4, corrected.")]
