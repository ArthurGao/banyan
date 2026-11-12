"""The role layer through FakeLLM (a Pydantic AI FunctionModel): outputs, the one validation retry,
timeouts, aborts and one usage row per attempt (design §7.2, §4)."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import BaseModel

from banyan.llm import CallContext, Role, RoleError, UsageCollector, load_roles
from banyan.llm.fakes import FakeLLM

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
    assert not client.configured(Role.NOTES_UPDATE)


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
