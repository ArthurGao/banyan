"""The path table of design §6.4: rows 1, 2 and 5 to 10 (rows 3, 4 and 4b, with the web, are in
test_web_paths.py), plus the skip rules of §6.2. Each asserts the roles called, the source, the
path and the jobs."""

import dataclasses
import re
from typing import Any

import pytest

from banyan.llm import Role, RoleError
from banyan.llm.fakes import FakeLLM
from banyan.pipeline.events import CitationEvent, PathEvent, TokenEvent
from banyan.steward import FakeSteward, RetrievalItem, RetrievalResult
from tests.conftest import FRESH, NOT_FRESH
from tests.pipeline.helpers import ANSWER, HIT, MISS, Outcome, Run, ask_input, entry, verdict


def jobs(outcome: Outcome) -> list[str]:
    return [j.kind for j in outcome.jobs]


async def test_row_1_a_judged_hit_answers_from_the_kb_with_citations() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["A Service is a stable IP [S2] for pods [S1][S2]."],
        }
    )
    run = Run(fake, FakeSteward(items=[entry("e1"), entry("e2"), entry("m1", "memory")]))
    out = await run(ask_input())
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.JUDGE_SUFFICIENCY, Role.ANSWER_FROM_KB]
    assert out.answer.source == "kb"
    assert out.steps() == [
        ("scope", "ok"),
        ("freshness", "ok"),
        ("retrieve", "ok"),
        ("judge", "hit"),
        ("answer", "ok"),
        ("writeback", "ok"),
    ]
    assert [(c.entry_id, c.item_id) for c in out.answer.citations] == [("e2", "e2"), ("e1", "e1")]
    assert {c.retrieval_id for c in out.answer.citations} <= set(out.retrieval_ids)
    assert jobs(out) == []  # an answer from the KB is not proposed back (design §6.2 step 6)
    from_kb = fake.calls_of(Role.ANSWER_FROM_KB)[0]
    assert 'id="S1" kind="kb"' in from_kb.prompt and 'id="M1" kind="memory"' in from_kb.prompt
    assert out.answer.roles[-2:] == [
        "judge.sufficiency:fake:fake-judge.sufficiency",
        "answer.from_kb:fake:fake-answer.from_kb",
    ]


async def test_an_uncited_kb_answer_fails() -> None:
    """Owner decision: no answer without provenance (row 7, not a silent route change)."""
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["Trust me, [S9] it works."],
        }
    )
    with pytest.raises(RoleError) as failed:
        await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert (failed.value.role, failed.value.kind) == (Role.ANSWER_FROM_KB, "validation")
    assert Role.ANSWER_COMPOSE not in fake.roles_called


async def test_row_2_a_judged_miss_composes_with_what_is_missing() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [MISS],
            Role.ANSWER_COMPOSE: [ANSWER],
        }
    )
    out = await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.JUDGE_SUFFICIENCY, Role.ANSWER_COMPOSE]
    assert (out.answer.source, jobs(out)) == ("llm", ["kb_propose"])
    assert out.detail("judge") == {"coverage": 0.3, "missing": ["NodePort details"]}
    compose = fake.calls_of(Role.ANSWER_COMPOSE)[0].prompt
    assert "NodePort details" in compose and "content e1" in compose
    assert out.answer.citations == []


async def test_a_sufficient_verdict_below_the_threshold_is_a_miss() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [verdict(True, 0.69)],
            Role.ANSWER_COMPOSE: [ANSWER],
        }
    )
    out = await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert out.answer.source == "llm" and ("judge", "miss") in out.steps()


async def test_row_5_force_llm_skips_freshness_and_judge_but_still_retrieves() -> None:
    fake = FakeLLM({Role.ANSWER_COMPOSE: [ANSWER]})
    steward = FakeSteward(items=[entry("e1")])
    out = await Run(fake, steward)(ask_input(force_llm=True))
    assert fake.roles_called == [Role.ANSWER_COMPOSE]
    assert out.detail("freshness") == out.detail("judge") == {"reason": "force_llm"}
    assert steward.retrievals and "content e1" in fake.calls_of(Role.ANSWER_COMPOSE)[0].prompt
    assert jobs(out) == ["kb_propose"]


async def test_row_6_retrieval_ok_but_no_entries_goes_to_compose() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    steward = FakeSteward()
    run = Run(fake, steward)
    out = await run(ask_input())
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.ANSWER_COMPOSE]
    assert out.answer.source == "llm" and out.detail("judge") == {"reason": "no_entries"}
    assert run.path_events() == [
        ("scope", "ok"),
        ("freshness", "ok"),
        ("retrieve", "ok"),
        ("judge", "skipped"),
        ("answer", "running"),
        ("writeback", "ok"),
    ]
    assert len(out.retrieval_ids) == 2  # node and topic; with one topic "all" is the topic
    tags = sorted((r["tags"] or [""])[0] for r in steward.retrievals)  # type: ignore[index]
    assert tags[0] == "" and tags[1].startswith("node:")
    assert "".join(e.text for e in run.events if isinstance(e, TokenEvent)) == ANSWER


async def test_row_7_a_failed_role_raises_and_queues_nothing() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: [ConnectionError("down")],
        }
    )
    run = Run(fake, FakeSteward(items=[entry("e1")]))
    with pytest.raises(RoleError) as failed:
        await run(ask_input())
    assert failed.value.role == Role.ANSWER_FROM_KB
    assert ("writeback", "ok") not in run.path_events()


async def test_row_7_a_failed_judge_is_a_failure_not_a_miss() -> None:
    fake = FakeLLM(
        {Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.JUDGE_SUFFICIENCY: ["not json", "still not json"]}
    )
    with pytest.raises(RoleError) as failed:
        await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert failed.value.role == Role.JUDGE_SUFFICIENCY
    assert Role.ANSWER_COMPOSE not in fake.roles_called  # never "treated as a miss"


@pytest.mark.parametrize(
    "steward",
    [FakeSteward(unavailable=True), FakeSteward(degraded=["kb"], items=[entry("e1")])],
    ids=["unreachable", "kb_degraded"],
)
async def test_row_8_steward_unavailable_still_answers(steward: FakeSteward) -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    out = await Run(fake, steward)(ask_input())
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.ANSWER_COMPOSE]
    assert ("retrieve", "unavailable") in out.steps()
    assert out.detail("judge") == {"reason": "retrieve_unavailable"}
    assert (out.answer.source, jobs(out)) == ("llm", ["kb_propose"])
    assert "content e1" not in fake.calls_of(Role.ANSWER_COMPOSE)[0].prompt


async def test_only_memory_degraded_is_not_a_degradation() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    out = await Run(fake, FakeSteward(degraded=["memory"]))(ask_input())
    assert ("retrieve", "ok") in out.steps()


async def test_row_9_an_explanation_from_the_kb_needs_the_higher_threshold() -> None:
    inp = ask_input(mode="explain", question='Explain "Services"', outline="- Services")
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH, NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [verdict(True, 0.8), verdict(True, 0.9)],
            Role.NODE_EXPLAIN: [ANSWER],
            Role.ANSWER_FROM_KB: ["From the KB [S1]."],
        }
    )
    below = await Run(fake, FakeSteward(items=[entry("e1")]))(inp)  # 0.8 < 0.85: a miss
    assert below.answer.source == "llm"
    hit = await Run(fake, FakeSteward(items=[entry("e1")]))(inp)  # 0.9: a hit
    assert (hit.answer.source, jobs(hit)) == ("kb", ["notes_update"])
    judge_prompt = fake.calls_of(Role.JUDGE_SUFFICIENCY)[0].prompt
    assert "Node summary: Stable addresses" in judge_prompt


async def test_row_10_an_explanation_miss_uses_node_explain_and_queues_both() -> None:
    inp = ask_input(mode="explain", question='Explain "Services"', outline="- Services")
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [MISS],
            Role.NODE_EXPLAIN: [ANSWER],
        }
    )
    out = await Run(fake, FakeSteward(items=[entry("e1")]))(inp)
    assert fake.roles_called == [Role.JUDGE_FRESHNESS, Role.JUDGE_SUFFICIENCY, Role.NODE_EXPLAIN]
    assert [(j.kind, j.dedupe_key) for j in out.jobs] == [
        ("kb_propose", None),
        ("notes_update", f"notes:{inp.node_id}"),
    ]


async def test_needs_web_skips_the_judge_and_warns_without_web_tools() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    out = await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input())
    assert out.detail("judge") == {"reason": "needs_web"}
    assert out.answer.warnings == ["stale_risk"]


async def test_without_a_freshness_role_the_step_is_skipped() -> None:
    fake = FakeLLM({Role.ANSWER_COMPOSE: [ANSWER]}, roles=[Role.ANSWER_COMPOSE])
    out = await Run(fake, FakeSteward())(ask_input())
    assert fake.roles_called == [Role.ANSWER_COMPOSE]
    assert out.detail("freshness") == {"reason": "role_not_configured"}


async def test_without_steward_retrieval_is_not_configured() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    out = await Run(fake, None)(ask_input())
    assert ("retrieve", "not_configured") in out.steps() and out.retrieval_ids == []


class ScopedSteward(FakeSteward):
    """Each scope returns its own items, as steward does for node, topic and all topics."""

    async def retrieve(self, query: str, **kw: Any) -> Any:
        widest = "topic-other" in kw["collections"]  # the other topic is only in the widest
        scope = "node" if kw.get("tags") else ("all" if widest else "topic")
        self.retrievals.append({"scope": scope})
        n = {"node": 3, "topic": 5, "all": 6}[scope]
        shared = [entry("shared")]  # every scope also finds this one
        return RetrievalResult(
            retrieval_id=scope,
            items=[*shared, *(entry(f"{scope}{i}") for i in range(n))],
            context=None,
            degraded=[],
        )


async def test_scopes_merge_nearest_first_dedupe_and_stop_at_top_k() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [MISS],
            Role.ANSWER_COMPOSE: [ANSWER],
        }
    )
    inp = ask_input()
    inp = inp.model_copy(update={"all_collections": [inp.collection, "topic-other"]})
    out = await Run(fake, ScopedSteward())(inp)
    assert out.detail("retrieve")["entries"] == 8  # deps.top_k
    judge_prompt = fake.calls_of(Role.JUDGE_SUFFICIENCY)[0].prompt
    labels = re.findall(r'id="(S\d+)" kind="kb">\nt(\w+)', judge_prompt)
    assert [name for _, name in labels] == [
        "shared",
        "node0",
        "node1",
        "node2",
        "topic0",
        "topic1",
        "topic2",
        "topic3",
    ]
    assert out.retrieval_ids == ["node", "topic", "all"]


async def test_an_uncited_kb_answer_is_recorded_as_a_failed_attempt() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["No sources here."],
        }
    )
    run = Run(fake, FakeSteward(items=[entry("e1")]))
    with pytest.raises(RoleError):
        await run(ask_input())
    rows = [(r.role, r.ok, r.error_kind) for r in run.deps.usage.records]
    assert rows[-1] == ("answer.from_kb", False, "validation")


async def test_a_single_topic_is_not_retrieved_twice() -> None:
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [NOT_FRESH], Role.ANSWER_COMPOSE: [ANSWER]})
    steward = FakeSteward()
    await Run(fake, steward)(ask_input())  # all_collections == [this topic]
    assert len(steward.retrievals) == 2  # node scope and topic scope; "all" equals "topic"


def long_entry(i: str, tokens: int) -> RetrievalItem:
    return dataclasses.replace(entry(i), content="x" * (tokens * 4))


async def test_the_merged_sources_stay_inside_the_context_budget() -> None:
    # Every scope gets the whole budget from steward; the merge must keep the total inside it.
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [MISS],
            Role.ANSWER_COMPOSE: [ANSWER],
        }
    )
    run = Run(fake, FakeSteward(items=[long_entry(f"e{i}", 1000) for i in range(5)]))
    out = await run(ask_input())
    assert run.deps.config.context_budget == 3000
    assert out.detail("retrieve")["entries"] == 2  # a third would make 3003 tokens


async def test_a_cited_document_is_not_recorded_as_a_kb_entry() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["From the doc [S1] and the entry [S2]."],
        }
    )
    run = Run(fake, FakeSteward(items=[entry("d1", "document"), entry("e1")]))
    out = await run(ask_input())
    assert [(c.entry_id, c.item_id) for c in out.answer.citations] == [(None, "d1"), ("e1", "e1")]


async def test_a_kb_answer_streams_its_citations_before_the_writeback() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["A stable IP [S2] for pods [S1]."],
        }
    )
    run = Run(fake, FakeSteward(items=[entry("e1"), entry("e2")]))
    await run(ask_input())
    kinds = [type(e).__name__ if not isinstance(e, PathEvent) else e.entry.step for e in run.events]
    citations = [e.citation for e in run.events if isinstance(e, CitationEvent)]
    assert [(c.kind, c.entry_id, c.title) for c in citations] == [
        ("kb", "e2", "te2"),
        ("kb", "e1", "te1"),
    ]
    assert kinds.index("CitationEvent") < kinds.index("writeback")


async def test_knowledge_the_learner_accepted_from_a_card_is_retrieved_with_the_topic() -> None:
    steward = FakeSteward()
    inbox = await steward.ensure_collection("inbox", review_policy={}, idempotency_key="c")
    await steward.create_entry(
        inbox, title="Generics erase types", content="At runtime a List<String> is a List.",
        tags=[], risk_level="low", idempotency_key="e",
    )  # fmt: skip
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [MISS],
            Role.ANSWER_COMPOSE: [ANSWER],
        }
    )
    inp = ask_input()
    await Run(fake, steward)(inp)
    assert "Generics erase types" in fake.calls_of(Role.JUDGE_SUFFICIENCY)[0].prompt
    # The node scope stays the node's; the topic scope adds the inbox, so with one topic the
    # widest scope is still the topic scope and is not asked again.
    assert [r["collections"] for r in steward.retrievals] == [
        [inp.collection],
        [inp.collection, "inbox"],
    ]


async def test_an_answer_from_the_kb_follows_the_topic_instructions() -> None:
    fake = FakeLLM(
        {
            Role.JUDGE_FRESHNESS: [NOT_FRESH],
            Role.JUDGE_SUFFICIENCY: [HIT],
            Role.ANSWER_FROM_KB: ["Stable IP [S1]."],
        }
    )
    await Run(fake, FakeSteward(items=[entry("e1")]))(ask_input(instructions="Use Java analogies."))
    assert "Use Java analogies." in fake.calls_of(Role.ANSWER_FROM_KB)[0].prompt
