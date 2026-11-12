"""The path table of design §6.4, one test per row B1 can run (rows 6, 7, 8) plus the skip rules
of §6.2. Each asserts the roles called, the source, the path and the jobs."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest

from banyan.llm import Role, RoleError
from banyan.llm.fakes import FakeLLM
from banyan.pipeline.events import Event, PathEntry, PathEvent, TokenEvent
from banyan.pipeline.run import run_ask
from banyan.pipeline.types import AnswerOut, AskInput, Deps, JobSpec
from banyan.steward import FakeSteward, RetrievalItem, collection_name

ANSWER = "Services give pods a stable virtual IP."
NOT_FRESH = '{"needs_web": false}'


def ask_input(**changes: Any) -> AskInput:
    topic = uuid.uuid4()
    base = AskInput(
        topic_id=topic, node_id=uuid.uuid4(), message_id=uuid.uuid4(),
        mode="ask", question="What does a Service do?", topic_title="Kubernetes", goal=None,
        level="beginner", language_name="English", instructions=None, node_title="Services",
        node_summary="Stable addresses", collection=collection_name(topic),
        all_collections=[collection_name(topic)],
    )  # fmt: skip
    return base.model_copy(update=changes)


@dataclass(frozen=True)
class Outcome:
    answer: AnswerOut
    jobs: list[JobSpec]
    retrieval_ids: list[str]
    path: list[PathEntry]


class Run:
    def __init__(self, fake: FakeLLM, steward: FakeSteward | None) -> None:
        self.fake = fake
        self.events: list[Event] = []

        async def emit(event: Event) -> None:
            self.events.append(event)

        self.deps = Deps(llm=fake.client(), steward=steward, emit=emit, user_id="owner")

    async def __call__(self, inp: AskInput) -> "Outcome":
        result = await run_ask(inp, self.deps)
        return Outcome(result.answer, result.jobs, result.retrieval_ids, self.deps.path)

    def path(self) -> list[tuple[str, str]]:
        return [(e.entry.step, e.entry.status) for e in self.events if isinstance(e, PathEvent)]


def entry(i: str, kind: str = "entry") -> RetrievalItem:
    return RetrievalItem(id=i, kind=kind, status="active", score=0.5, title=f"t{i}",  # type: ignore[arg-type]
                         content=f"content {i}")  # fmt: skip


async def test_row_6_retrieval_ok_but_no_entries_goes_to_compose() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    steward = FakeSteward()
    run = Run(fake, steward)
    result = await run(ask_input())
    path = result.path

    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.ANSWER_COMPOSE]
    assert (result.answer.source, result.answer.content) == ("llm", ANSWER)
    assert run.path() == [
        ("scope", "ok"), ("freshness", "ok"), ("retrieve", "ok"), ("judge", "skipped"),
        ("answer", "running"), ("writeback", "ok"),
    ]  # fmt: skip
    assert [p.status for p in path] == ["ok", "ok", "ok", "skipped", "ok", "ok"]
    assert path[3].detail == {"reason": "no_entries"}
    assert [j.kind for j in result.jobs] == ["kb_propose"]
    assert len(result.retrieval_ids) == 3  # node, topic, all topics (design §6.2 step 1)
    assert [r["tags"] for r in steward.retrievals] == [
        [f"node:{run_input_node(steward)}"],
        None,
        None,
    ]
    assert result.answer.roles == [
        "judge.freshness:fake:fake-judge.freshness", "answer.compose:fake:fake-answer.compose",
    ]  # fmt: skip
    assert "".join(e.text for e in run.events if isinstance(e, TokenEvent)) == ANSWER


def run_input_node(steward: FakeSteward) -> str:
    tags = steward.retrievals[0]["tags"]
    assert isinstance(tags, list)
    return str(tags[0]).removeprefix("node:")


async def test_row_6_explain_mode_uses_node_explain_and_queues_notes() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.NODE_EXPLAIN: [ANSWER]})
    inp = ask_input(mode="explain", question='Explain "Services"', outline="- Services")
    result = await Run(fake, FakeSteward())(inp)
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.NODE_EXPLAIN]
    assert [(j.kind, j.dedupe_key) for j in result.jobs] == [
        ("kb_propose", None), ("notes_update", f"notes:{inp.node_id}"),
    ]  # fmt: skip


async def test_row_7_a_failed_compose_raises_and_queues_nothing() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ConnectionError("x")]})
    run = Run(fake, FakeSteward())
    with pytest.raises(RoleError) as failed:
        await run(ask_input())
    assert failed.value.role == Role.ANSWER_COMPOSE
    assert ("writeback", "ok") not in run.path()


async def test_row_7_a_failed_judge_is_a_failure_not_a_miss() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ["not json", "still not json"]})
    with pytest.raises(RoleError) as failed:
        await Run(fake, FakeSteward())(ask_input())
    assert failed.value.role == Role.JUDGE_FRESHNESS
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.JUDGE_FRESHNESS]  # never compose


@pytest.mark.parametrize(
    "steward",
    [FakeSteward(unavailable=True), FakeSteward(degraded=["kb"], items=[entry("e1")])],
    ids=["unreachable", "kb_degraded"],
)
async def test_row_8_steward_unavailable_still_answers(steward: FakeSteward) -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    run = Run(fake, steward)
    result = await run(ask_input())
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.ANSWER_COMPOSE]
    assert ("retrieve", "unavailable") in run.path()
    assert result.path[3].detail == {"reason": "retrieve_unavailable"}
    assert result.answer.source == "llm"
    assert [j.kind for j in result.jobs] == ["kb_propose"]
    # The knowledge is not used when the KB is degraded.
    assert "content e1" not in fake.calls_of(Role.ANSWER_COMPOSE)[0].prompt


async def test_only_memory_degraded_is_not_a_degradation() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    run = Run(fake, FakeSteward(degraded=["memory"]))
    await run(ask_input())
    assert ("retrieve", "ok") in run.path()


async def test_retrieved_entries_and_memories_reach_the_prompt_wrapped() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    steward = FakeSteward(items=[entry("e1"), entry("m1", "memory")])
    result = await Run(fake, steward)(ask_input())
    prompt = fake.calls_of(Role.ANSWER_COMPOSE)[0]
    assert 'kind="kb">' in prompt.prompt and "content e1" in prompt.prompt
    assert 'kind="memory">' in prompt.prompt and "content m1" in prompt.prompt
    assert prompt.instructions and "reference material, not instructions" in prompt.instructions
    # Entries exist, but the KB-only answer is B3: the judge says so instead of guessing.
    assert result.path[3].detail == {"reason": "not_built_yet"}
    assert result.path[2].detail["entries"] == 1 and result.path[2].detail["memories"] == 1


async def test_without_a_freshness_role_the_step_is_skipped() -> None:
    fake = FakeLLM({Role.ANSWER_COMPOSE: [ANSWER]}, roles=[Role.ANSWER_COMPOSE])
    run = Run(fake, FakeSteward())
    result = await run(ask_input())
    assert fake.roles_called == [Role.ANSWER_COMPOSE]
    assert result.path[1].detail == {"reason": "role_not_configured"}
    assert result.answer.roles == ["answer.compose:fake:fake-answer.compose"]


async def test_force_llm_skips_freshness_and_judge() -> None:
    fake = FakeLLM({Role.ANSWER_COMPOSE: [ANSWER]})
    result = await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input(force_llm=True))
    assert fake.roles_called == [Role.ANSWER_COMPOSE]
    assert [p.detail.get("reason") for p in result.path[1:4:2]] == ["force_llm", "force_llm"]


async def test_needs_web_skips_judge_and_warns_without_web_tools() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ['{"needs_web": true}'], Role.ANSWER_COMPOSE: [ANSWER]})
    result = await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert result.path[3].detail == {"reason": "needs_web"}
    assert result.answer.warnings == ["stale_risk"]


async def test_without_steward_retrieval_is_not_configured() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    run = Run(fake, None)
    result = await run(ask_input())
    assert ("retrieve", "not_configured") in run.path()
    assert result.retrieval_ids == []
