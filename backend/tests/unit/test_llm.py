"""The role layer through FakeLLM (a Pydantic AI FunctionModel): outputs, the one validation retry,
timeouts, aborts and one usage row per attempt (design §7.2, §4)."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from banyan.llm import CallContext, Role, RoleError, ToolCalled, UsageCollector, load_roles
from banyan.llm.fakes import FakeLLM, ToolCall

CTX = CallContext(path_kind="test")


class Verdict(BaseModel):
    needs_web: bool


def rows(usage: UsageCollector) -> list[tuple[str, int, bool, str | None]]:
    return [(r.role, r.attempt, r.ok, r.error_kind) for r in usage.records]


async def structured(fake: FakeLLM, usage: UsageCollector, *, timeout_s: float = 5.0) -> Verdict:
    return await fake.client(timeout_s=timeout_s).structured(
        Role.JUDGE_FRESHNESS, Verdict, instructions="judge", prompt="q", context=CTX, usage=usage
    )


async def test_structured_output_and_one_usage_row() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ['{"needs_web": true}']})
    assert await structured(fake, usage) == Verdict(needs_web=True)
    assert rows(usage) == [("judge.freshness", 1, True, None)]
    (row,) = usage.records
    assert (row.provider, row.model, row.context) == ("fake", "fake-judge.freshness", CTX)
    assert row.input_tokens is not None and row.input_tokens > 0
    assert fake.calls_of(Role.JUDGE_FRESHNESS)[0].instructions == "judge"


async def test_invalid_output_is_retried_once_on_the_same_model() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ['{"needs_web": "maybe"}', '{"needs_web": false}']})
    assert await structured(fake, usage) == Verdict(needs_web=False)
    assert rows(usage) == [
        ("judge.freshness", 1, False, "validation"),
        ("judge.freshness", 2, True, None),
    ]


async def test_invalid_output_twice_fails_with_both_attempts_recorded() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ["not json", "{}"]})
    with pytest.raises(RoleError) as failed:
        await structured(fake, usage)
    assert (failed.value.role, failed.value.kind) == (Role.JUDGE_FRESHNESS, "validation")
    assert rows(usage) == [
        ("judge.freshness", 1, False, "validation"),
        ("judge.freshness", 2, False, "validation"),
    ]


async def test_a_provider_error_fails_at_once_without_a_retry() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.JUDGE_FRESHNESS: [ConnectionError("refused"), '{"needs_web": true}']})
    with pytest.raises(RoleError) as failed:
        await structured(fake, usage)
    assert failed.value.kind == "provider"
    assert rows(usage) == [("judge.freshness", 1, False, "provider")]


async def test_a_timeout_is_a_failure_with_a_usage_row() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.JUDGE_FRESHNESS: ['{"needs_web": true}']}, delay_s=0.5)
    with pytest.raises(RoleError) as failed:
        await structured(fake, usage, timeout_s=0.05)
    assert failed.value.kind == "timeout"
    assert rows(usage) == [("judge.freshness", 1, False, "timeout")]


async def test_streaming_yields_the_text_and_records_usage_at_the_end() -> None:
    usage = UsageCollector()
    text = "Pods share a network namespace, so containers in one pod talk over localhost."
    fake = FakeLLM({Role.NODE_EXPLAIN: [text]})
    chunks = [
        c
        async for c in fake.client().stream(
            Role.NODE_EXPLAIN, instructions="explain", prompt="pods", context=CTX, usage=usage
        )
    ]
    assert len(chunks) > 1 and "".join(chunks) == text
    assert rows(usage) == [("node.explain", 1, True, None)]
    assert usage.output_tokens > 0


async def test_a_stream_closed_midway_is_recorded_as_aborted() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.ANSWER_COMPOSE: ["x" * 200]}, delay_s=0.01)
    stream = fake.client().stream(
        Role.ANSWER_COMPOSE, instructions="", prompt="q", context=CTX, usage=usage
    )
    assert await anext(stream)
    await stream.aclose()
    assert rows(usage) == [("answer.compose", 1, False, "aborted")]


async def test_a_cancelled_stream_is_recorded_as_aborted() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.ANSWER_COMPOSE: ["x" * 400]}, delay_s=0.05)

    async def consume() -> None:
        async for _ in fake.client().stream(
            Role.ANSWER_COMPOSE, instructions="", prompt="q", context=CTX, usage=usage
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.12)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert rows(usage) == [("answer.compose", 1, False, "aborted")]


async def test_a_role_without_a_model_fails() -> None:
    fake = FakeLLM(roles=[Role.NODE_EXPLAIN])
    client = fake.client()
    assert not client.configured(Role.JUDGE_FRESHNESS)
    with pytest.raises(RoleError, match="no model"):
        await client.structured(
            Role.JUDGE_FRESHNESS, Verdict, instructions="", prompt="", context=CTX,
            usage=UsageCollector(),
        )  # fmt: skip


VALID = {
    "providers": {
        "anthropic": {"kind": "anthropic", "api_key_env": "TEST_ANTHROPIC_KEY"},
        "ollama": {"kind": "openai_compatible", "base_url": "http://127.0.0.1:11434/v1"},
    },
    "models": [
        {"provider": "anthropic", "name": "strong", "tools": True, "structured": True,
         "context": 200000},
        {"provider": "ollama", "name": "qwen3:8b", "structured": True, "context": 32000},
    ],
    "roles": {
        "outline.generate": "anthropic/strong",
        "node.explain": "anthropic/strong",
        "answer.compose": "anthropic/strong",
        "judge.freshness": "ollama/qwen3:8b",
        "notes.update": "ollama/qwen3:8b",
        "judge.sufficiency": "ollama/qwen3:8b",
        "answer.from_kb": "ollama/qwen3:8b",
        "kb.draft_entry": "ollama/qwen3:8b",
        "node.classify": "ollama/qwen3:8b",
        "branch.compress": "ollama/qwen3:8b",
    },
}  # fmt: skip


def write(tmp_path: Path, config: object) -> Path:
    path = tmp_path / "roles.yaml"
    path.write_text(json.dumps(config))  # JSON is YAML
    return path


def test_a_valid_config_loads_and_builds_every_role(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "k")
    config = load_roles(write(tmp_path, VALID))
    from banyan.llm import RoleClient

    client = RoleClient.from_config(config, timeout_s=10)
    assert client.provenance(Role.JUDGE_FRESHNESS) == "judge.freshness:ollama:qwen3:8b"
    assert client.configured(Role.OUTLINE_GENERATE)
    # An optional role left unmapped is not configured (branch.compress, the example until B6,
    # is required now; judge.freshness is the optional one left).
    roles = {k: v for k, v in VALID["roles"].items() if k != "judge.freshness"}  # type: ignore[attr-defined]
    partial = RoleClient.from_config(
        load_roles(write(tmp_path, VALID | {"roles": roles})), timeout_s=10
    )
    assert not partial.configured(Role.JUDGE_FRESHNESS)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda c: c["roles"].pop("node.explain"), "role node.explain has no model"),
        (lambda c: c["roles"].update({"notes.update": "anthropic/nope"}), "not registered"),
        (lambda c: c["roles"].update({"answer.compose": "ollama/qwen3:8b"}), "tool calls"),
        (lambda c: c["models"][1].update({"structured": False}), "structured output"),
        (lambda c: c["models"][1].update({"provider": "nobody"}), "unknown provider"),
        (lambda c: c["roles"].update({"no.such.role": "ollama/qwen3:8b"}), "no.such.role"),
    ],
)
def test_an_inconsistent_config_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: object, message: str
) -> None:
    monkeypatch.setenv("TEST_ANTHROPIC_KEY", "k")
    config = json.loads(json.dumps(VALID))
    change(config)  # type: ignore[operator]
    with pytest.raises(ValueError, match=message):
        load_roles(write(tmp_path, config))


def test_a_missing_api_key_variable_fails_at_startup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TEST_ANTHROPIC_KEY", raising=False)
    with pytest.raises(ValueError, match="TEST_ANTHROPIC_KEY"):
        load_roles(write(tmp_path, VALID))


def test_the_role_layers_error_kinds_are_the_databases() -> None:
    """Defined twice on purpose (banyan.llm imports no models); they must not drift."""
    from typing import get_args

    from banyan.llm.usage import ErrorKind
    from banyan.models import ErrorKind as StoredErrorKind

    assert set(get_args(ErrorKind)) == {k.value for k in StoredErrorKind}


async def collect(fake: FakeLLM, usage: UsageCollector, **kw: object) -> list[object]:
    stream = fake.client().stream(
        Role.ANSWER_COMPOSE, instructions="answer", prompt="q", context=CTX, usage=usage, **kw
    )  # type: ignore[arg-type]
    return [item async for item in stream]


def texts_and_calls(items: list[object]) -> list[object]:
    """Adjacent text chunks joined, so the assertion does not depend on chunk sizes."""
    out: list[object] = []
    for item in items:
        if isinstance(item, str) and out and isinstance(out[-1], str):
            out[-1] += item
        else:
            out.append(item)
    return out


async def test_a_tool_loop_reports_each_call_in_order_and_returns_the_final_answer() -> None:
    searched: list[str] = []

    async def web_search(query: str) -> str:
        """Search the web."""
        searched.append(query)
        return "W1: Kubernetes 1.40 is the latest release."

    usage = UsageCollector()
    fake = FakeLLM(
        {
            Role.ANSWER_COMPOSE: [
                ToolCall("web_search", {"query": "latest kubernetes"}, preamble="Let me look. "),
                "It is 1.40 [W1].",
            ]
        }
    )
    items = await collect(fake, usage, tools=[web_search], max_steps=4)
    # The text before the call streams too; the client drops it at the `tool` event (design §9).
    assert texts_and_calls(items) == [
        "Let me look. ",
        ToolCalled("web_search", {"query": "latest kubernetes"}),
        "It is 1.40 [W1].",
    ]
    assert searched == ["latest kubernetes"]
    assert fake.calls_of(Role.ANSWER_COMPOSE)[1].tool_returns == [
        "W1: Kubernetes 1.40 is the latest release."
    ]
    assert rows(usage) == [("answer.compose", 1, True, None)]  # one row for the whole loop
    assert usage.input_tokens > 0


async def test_a_loop_over_its_step_budget_fails_as_budget_and_keeps_its_usage() -> None:
    async def web_search(query: str) -> str:
        """Search the web."""
        return "nothing"

    usage = UsageCollector()
    fake = FakeLLM({Role.ANSWER_COMPOSE: [ToolCall("web_search", {"query": "x"})] * 5})
    with pytest.raises(RoleError) as caught:
        await collect(fake, usage, tools=[web_search], max_steps=2)
    assert caught.value.kind == "budget"
    assert rows(usage) == [("answer.compose", 1, False, "budget")]
    assert usage.input_tokens > 0  # the two requests made are paid for


async def test_the_output_cap_reaches_the_model() -> None:
    usage = UsageCollector()
    fake = FakeLLM({Role.ANSWER_COMPOSE: ["short"]})
    await collect(fake, usage, max_output_tokens=2000)
    assert fake.calls_of(Role.ANSWER_COMPOSE)[0].max_tokens == 2000


async def test_a_validated_stream_checks_the_final_answer_only() -> None:
    async def web_search(query: str) -> str:
        """Search the web."""
        return "W1: x"

    seen: list[str] = []
    usage = UsageCollector()
    fake = FakeLLM(
        {Role.ANSWER_COMPOSE: [ToolCall("web_search", {"query": "x"}, preamble="Hmm. "), "Done."]}
    )
    await collect(fake, usage, tools=[web_search], validate=seen.append)
    assert seen == ["Done."]


async def test_a_loop_over_its_total_token_budget_fails_as_budget() -> None:
    async def web_search(query: str) -> str:
        """Search the web."""
        return "a result " * 50

    usage = UsageCollector()
    fake = FakeLLM(
        {
            Role.ANSWER_COMPOSE: [
                ToolCall("web_search", {"query": "x"}),
                ToolCall("web_search", {"query": "y"}),
                "Done.",
            ]
        }
    )
    with pytest.raises(RoleError) as caught:
        # Each request is small; together they pass the loop's total.
        await collect(fake, usage, tools=[web_search], max_steps=10, max_total_tokens=150)
    assert caught.value.kind == "budget"
    assert rows(usage) == [("answer.compose", 1, False, "budget")]
