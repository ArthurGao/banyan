"""Calling a role: one model, a timeout, one retry on invalid structured output, a usage row per
attempt (design §7.2). There is no fallback: a failure raises `RoleError`."""

import asyncio
import os
import time
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model

from banyan.llm.config import ProviderConfig, RolesConfig
from banyan.llm.roles import Role
from banyan.llm.usage import CallContext, ErrorKind, UsageCollector, UsageRecord

T = TypeVar("T", bound=BaseModel)

STRUCTURED_ATTEMPTS = 2  # the first try and one retry on invalid output, same model


class RoleError(Exception):
    """A role call failed (design §6.4 row 7): the caller stops; nothing is retried elsewhere."""

    def __init__(self, role: Role, kind: ErrorKind, detail: str) -> None:
        super().__init__(f"{role.value} failed ({kind}): {detail}")
        self.role = role
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class RoleModel:
    """A role's model, named for provenance (`role:provider:model`, AGENTS.md rule 5)."""

    provider: str
    name: str
    model: Model


class RoleClient:
    def __init__(self, models: Mapping[Role, RoleModel], *, timeout_s: float) -> None:
        self._models = dict(models)
        self._timeout_s = timeout_s

    @classmethod
    def from_config(cls, config: RolesConfig, *, timeout_s: float) -> "RoleClient":
        models = {}
        for role in config.roles:
            spec = config.model_for(role)
            assert spec is not None  # RolesConfig validated the mapping
            provider = config.providers[spec.provider]
            models[role] = RoleModel(spec.provider, spec.name, _build(provider, spec.name))
        return cls(models, timeout_s=timeout_s)

    def roles(self) -> list[Role]:
        return sorted(self._models)

    def configured(self, role: Role) -> bool:
        return role in self._models

    def provenance(self, role: Role) -> str:
        m = self._model(role)
        return f"{role.value}:{m.provider}:{m.name}"

    async def structured(
        self,
        role: Role,
        output_type: type[T],
        *,
        instructions: str,
        prompt: str,
        context: CallContext,
        usage: UsageCollector,
    ) -> T:
        m = self._model(role)
        agent = Agent(
            m.model, output_type=output_type, instructions=instructions, retries={"output": 0}
        )
        for number in range(1, STRUCTURED_ATTEMPTS + 1):
            attempt = _Attempt(role, m, number, context, usage)
            with capture_run_messages() as messages:
                try:
                    async with asyncio.timeout(self._timeout_s):
                        result = await agent.run(prompt)
                except BaseException as exc:
                    error = attempt.failed(exc, messages)
                    if isinstance(error, RoleError) and error.kind == "validation":
                        if number < STRUCTURED_ATTEMPTS:
                            continue  # one retry on invalid output, same model (design §7.2)
                    raise error from exc
            attempt.succeeded(result.usage.input_tokens, result.usage.output_tokens)
            return result.output
        raise AssertionError("unreachable")

    async def stream(
        self,
        role: Role,
        *,
        instructions: str,
        prompt: str,
        context: CallContext,
        usage: UsageCollector,
    ) -> AsyncIterator[str]:
        """Text deltas as they arrive. The usage row is added when the stream ends, however it ends
        (a cancelled or closed stream is `aborted`, design §9)."""
        m = self._model(role)
        agent = Agent(m.model, instructions=instructions)
        attempt = _Attempt(role, m, 1, context, usage)
        with capture_run_messages() as messages:
            try:
                async with asyncio.timeout(self._timeout_s):
                    async with agent.run_stream(prompt) as result:
                        async for delta in result.stream_text(delta=True, debounce_by=None):
                            yield delta
                        run = result.usage
            except BaseException as exc:
                raise attempt.failed(exc, messages) from exc
        attempt.succeeded(run.input_tokens, run.output_tokens)

    def _model(self, role: Role) -> RoleModel:
        try:
            return self._models[role]
        except KeyError:
            raise RoleError(role, "provider", "role has no model configured") from None


class _Attempt:
    """One provider attempt: it ends in exactly one usage row (design §4)."""

    def __init__(
        self, role: Role, m: RoleModel, number: int, context: CallContext, usage: UsageCollector
    ) -> None:
        self.role, self.m, self.number, self.context, self.usage = role, m, number, context, usage
        self.started = time.monotonic()

    def succeeded(self, input_tokens: int | None, output_tokens: int | None) -> None:
        self._row(input_tokens, output_tokens, None)

    def failed(self, exc: BaseException, messages: list[ModelMessage]) -> BaseException:
        """Records the attempt and returns what to raise. The role layer is a complete boundary:
        cancellation stays cancellation, every other failure becomes a RoleError."""
        kind = _classify(exc)
        self._row(*_tokens(messages), kind)
        if kind == "aborted":
            return exc
        return RoleError(self.role, kind, str(exc) or type(exc).__name__)

    def _row(
        self, input_tokens: int | None, output_tokens: int | None, error: ErrorKind | None
    ) -> None:
        self.usage.add(
            UsageRecord(
                role=self.role.value,
                provider=self.m.provider,
                model=self.m.name,
                attempt=self.number,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                ok=error is None,
                error_kind=error,
                latency_ms=int((time.monotonic() - self.started) * 1000),
                context=self.context,
            )
        )


def _classify(exc: BaseException) -> ErrorKind:
    if isinstance(exc, asyncio.CancelledError | GeneratorExit):
        return "aborted"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, UnexpectedModelBehavior):
        return "validation"  # Pydantic AI's "output did not validate" (output retries are 0)
    return "provider"


def _tokens(messages: list[ModelMessage]) -> tuple[int | None, int | None]:
    """Tokens the provider reported for a failed attempt; None when it reported nothing."""
    responses = [m for m in messages if isinstance(m, ModelResponse)]
    if not responses:
        return None, None
    return (
        sum(r.usage.input_tokens for r in responses),
        sum(r.usage.output_tokens for r in responses),
    )


def _build(provider: ProviderConfig, name: str) -> Model:
    key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
    if provider.kind == "anthropic":
        from pydantic_ai.models.anthropic import AnthropicModel
        from pydantic_ai.providers.anthropic import AnthropicProvider

        return AnthropicModel(name, provider=AnthropicProvider(api_key=key))
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    # `openai_compatible` (DeepSeek, Kimi, Ollama, llm-gateway) is `openai` with a base_url.
    return OpenAIChatModel(
        name, provider=OpenAIProvider(base_url=provider.base_url, api_key=key or "unused")
    )
