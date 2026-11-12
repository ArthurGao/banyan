"""FakeLLM (AGENTS.md rule 8): scripted answers per role, served through Pydantic AI's FunctionModel
so tests run the real role layer (timeouts, the validation retry, usage rows) without a vendor.

A script entry is the text the model returns (JSON for structured roles), an exception to raise,
a callable that gets the prompt, or a `ToolCall` (one model request of an answer loop that calls a
tool; the next entry answers once the tool has returned). When a role's queue is empty the
`default` answers; without one the call fails, so a test never gets an answer it did not script.
"""

import asyncio
import json
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelResponsePart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, FunctionModel

from banyan.llm.client import RoleClient, RoleModel
from banyan.llm.roles import Role


@dataclass(frozen=True)
class ToolCall:
    """A scripted model request that calls `name` with `args`, after saying `preamble`."""

    name: str
    args: dict[str, object]
    preamble: str = ""


Script = str | BaseException | Callable[[str], str] | ToolCall
STREAM_CHUNK = 12


@dataclass(frozen=True)
class FakeCall:
    role: Role
    instructions: str | None
    prompt: str
    tool_returns: list[str] = field(default_factory=list)  # what the tools answered just before
    max_tokens: int | None = None
    tools: list[str] = field(default_factory=list)  # the tools the model was offered


@dataclass
class FakeLLM:
    scripts: Mapping[Role, Sequence[Script]] = field(default_factory=dict)
    default: Callable[[Role, str], str] | None = None
    roles: Iterable[Role] = tuple(Role)
    delay_s: float = 0.0  # before every answer and between stream chunks (timeouts, aborts)
    calls: list[FakeCall] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._queues: dict[Role, deque[Script]] = defaultdict(deque)
        for role, entries in self.scripts.items():
            self._queues[role].extend(entries)

    def script(self, role: Role, *entries: Script) -> None:
        self._queues[role].extend(entries)

    def client(self, *, timeout_s: float = 5.0) -> RoleClient:
        models = {
            role: RoleModel("fake", f"fake-{role.value}", self._model(role)) for role in self.roles
        }
        return RoleClient(models, timeout_s=timeout_s)

    def calls_of(self, role: Role) -> list[FakeCall]:
        return [c for c in self.calls if c.role == role]

    @property
    def roles_called(self) -> list[Role]:
        return [c.role for c in self.calls]

    def _model(self, role: Role) -> FunctionModel:
        async def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            await asyncio.sleep(self.delay_s)
            answer = self._next(role, messages, info)
            if isinstance(answer, ToolCall):
                parts: list[ModelResponsePart] = (
                    [TextPart(answer.preamble)] if answer.preamble else []
                )
                parts.append(ToolCallPart(answer.name, answer.args, tool_call_id=self._call_id()))
                return ModelResponse(parts=parts)
            if info.output_tools:
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])
            return ModelResponse(parts=[TextPart(answer)])

        async def stream(
            messages: list[ModelMessage], info: AgentInfo
        ) -> AsyncIterator[str | dict[int, DeltaToolCall]]:
            answer = self._next(role, messages, info)
            text = answer.preamble if isinstance(answer, ToolCall) else answer
            for i in range(0, len(text), STREAM_CHUNK):
                await asyncio.sleep(self.delay_s)
                yield text[i : i + STREAM_CHUNK]
            if isinstance(answer, ToolCall):
                yield {
                    1: DeltaToolCall(
                        name=answer.name, json_args=json.dumps(answer.args),
                        tool_call_id=self._call_id(),
                    )
                }  # fmt: skip

        return FunctionModel(respond, stream_function=stream, model_name=f"fake-{role.value}")

    def _call_id(self) -> str:
        self._calls_made = getattr(self, "_calls_made", 0) + 1
        return f"call-{self._calls_made}"

    def _next(self, role: Role, messages: list[ModelMessage], info: AgentInfo) -> str | ToolCall:
        prompt = _last_prompt(messages)
        settings = info.model_settings or {}
        self.calls.append(
            FakeCall(
                role, info.instructions, prompt, _tool_returns(messages),
                settings.get("max_tokens"), [t.name for t in info.function_tools],
            )
        )  # fmt: skip
        queue = self._queues[role]
        script: Script
        if queue:
            script = queue.popleft()
        elif self.default is not None:
            script = self.default(role, prompt)
        else:
            raise AssertionError(f"FakeLLM: no script left for {role.value}")
        if isinstance(script, BaseException):
            raise script
        if isinstance(script, ToolCall):
            return script
        return script(prompt) if callable(script) else script


def _last_prompt(messages: list[ModelMessage]) -> str:
    for message in reversed(messages):
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                    return part.content
    return ""


def _tool_returns(messages: list[ModelMessage]) -> list[str]:
    last = messages[-1] if messages else None
    if not isinstance(last, ModelRequest):
        return []
    return [str(p.content) for p in last.parts if isinstance(p, ToolReturnPart)]


def demo_answers(role: Role, prompt: str) -> str:
    """`BANYAN_FAKES=1`: plausible canned answers so the frontend can run the whole flow."""
    if role == Role.OUTLINE_GENERATE:
        return json.dumps(
            {
                "nodes": [
                    {
                        "title": f"Part {i}: fake outline node",
                        "summary": "Generated by the fake model (BANYAN_FAKES=1).",
                        "children": [
                            {"title": f"Part {i}.{j}", "summary": "Fake sub-node.", "children": []}
                            for j in (1, 2)
                        ],
                    }
                    for i in (1, 2, 3)
                ]
            }
        )
    if role == Role.NOTES_UPDATE:
        return json.dumps(
            {
                "summary": "Fake notes (BANYAN_FAKES=1).",
                "key_points": ["A fake key point"],
                "examples": [],
                "pitfalls": [],
                "branch_highlights": [],
            }
        )
    if role == Role.JUDGE_SUFFICIENCY:
        # The demo answers from the KB whenever there is any knowledge to answer from.
        has_sources = "<source-" in prompt
        return json.dumps({"sufficient": has_sources, "coverage": 0.9 if has_sources else 0.0})
    if role == Role.ANSWER_FROM_KB:
        return "**Fake answer from the knowledge base** (BANYAN_FAKES=1) [S1]."
    if role == Role.KB_DRAFT_ENTRY:
        return json.dumps(
            {"title": "Fake entry", "content": prompt[-500:] or "fake", "risk_level": "low"}
        )
    if role == Role.JUDGE_FRESHNESS:
        return json.dumps({"needs_web": False, "reason": "fake model"})
    return (
        "**Fake answer** (BANYAN_FAKES=1). This text streams in small chunks so the client can "
        f"show progress.\n\nYou asked: {prompt[-200:]}"
    )
