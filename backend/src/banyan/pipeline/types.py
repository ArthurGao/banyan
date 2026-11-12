"""Step inputs and outputs (design §6.0): serializable, no shared state; I/O goes through `Deps`."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from banyan.llm import RoleClient, UsageCollector
from banyan.models import Source
from banyan.pipeline.events import Event, PathEntry
from banyan.steward import StewardReader

Mode = Literal["explain", "ask"]


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskInput(BaseModel):
    """Everything the pipeline needs about one question, loaded by the caller (design §6.1)."""

    topic_id: uuid.UUID
    node_id: uuid.UUID
    message_id: uuid.UUID  # the streaming placeholder this answer fills (design §9)
    mode: Mode
    question: str
    force_llm: bool = False  # allow_web arrives with the web tools (B4)
    # Context, loaded by the caller
    topic_title: str
    goal: str | None
    level: str
    language_name: str
    instructions: str | None
    node_title: str
    node_summary: str | None
    outline: str = ""  # explain: the outline as an indented list
    explanation: str | None = None  # ask: the node's explanation
    history: list[Turn] = Field(default_factory=list)  # ask: earlier turns, trimmed to budget
    collection: str  # this topic's steward collection name
    all_collections: list[str]  # every active topic's, for the widest scope


class Scope(BaseModel):
    collections: list[str]
    tags: list[str] = Field(default_factory=list)


class ScopeOut(BaseModel):
    scopes: list[Scope]


class FreshnessOut(BaseModel):
    skipped_because: str | None = None
    needs_web: bool = False

    @property
    def ran(self) -> bool:
        return self.skipped_because is None


class FreshnessVerdict(BaseModel):
    """`judge.freshness`'s structured output."""

    needs_web: bool
    reason: str = ""


class RetrievedSource(BaseModel):
    id: str
    title: str | None
    content: str


class RetrieveOut(BaseModel):
    status: Literal["ok", "unavailable", "not_configured"]
    retrieval_ids: list[str] = Field(default_factory=list)
    entries: list[RetrievedSource] = Field(default_factory=list)
    memories: list[RetrievedSource] = Field(default_factory=list)


class JudgeOut(BaseModel):
    hit: bool
    skipped_because: str | None = None


class AnswerOut(BaseModel):
    content: str
    source: Source
    roles: list[str]  # role:provider:model of every role that answered (provenance)
    warnings: list[str] = Field(default_factory=list)


class JobSpec(BaseModel):
    kind: str
    idempotency_key: str
    payload: dict[str, Any]
    dedupe_key: str | None = None


class WritebackOut(BaseModel):
    jobs: list[JobSpec]


class AskResult(BaseModel):
    """What the record step commits (the path comes from `Deps.path`)."""

    answer: AnswerOut
    retrieval_ids: list[str]
    cited_entry_ids: list[str]
    jobs: list[JobSpec]


@dataclass
class Deps:
    llm: RoleClient
    steward: StewardReader | None
    emit: Callable[[Event], Awaitable[None]]
    user_id: str
    top_k: int = 8
    context_budget: int = 3000
    usage: UsageCollector = field(default_factory=UsageCollector)
    # The path so far: the orchestrator appends, and a failed or aborted answer is recorded with it.
    path: list[PathEntry] = field(default_factory=list)
