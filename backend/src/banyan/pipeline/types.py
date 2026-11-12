"""Step inputs and outputs (design §6.0): serializable, no shared state; I/O goes through `Deps`."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from banyan.llm import RoleClient, UsageCollector
from banyan.models import Source
from banyan.pipeline.events import Event, PathEntry
from banyan.steward import RetrievalItem, StewardReader
from banyan.tools.search import SearchResult
from banyan.tools.web import Web

Mode = Literal["explain", "ask"]


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class WebSource(BaseModel):
    """A web page an answer may cite: a search result, a fetched page, or the topic's research
    (`topics.research_sources`, design §5.1 step 3)."""

    url: str
    title: str = ""
    summary: str = ""
    fetched_at: datetime

    @classmethod
    def from_result(cls, result: SearchResult, at: datetime) -> "WebSource":
        return cls(url=result.url, title=result.title, summary=result.snippet, fetched_at=at)


class AskInput(BaseModel):
    """Everything the pipeline needs about one question, loaded by the caller (design §6.1)."""

    topic_id: uuid.UUID
    node_id: uuid.UUID
    message_id: uuid.UUID  # the streaming placeholder this answer fills (design §9)
    branch_id: uuid.UUID  # where the answer goes
    episode_due: bool = False  # ask: with this turn the branch is due for an episode (§8.5)
    mode: Mode
    question: str
    force_llm: bool = False
    allow_web: bool = True  # false: no web tools; a time-sensitive answer warns (row 4b)
    generation_run_id: uuid.UUID | None = None  # set by a "generate all" run (design §5.1.1)
    supersedes: uuid.UUID | None = None  # a re-answer after "wrong"/"incomplete" (design §6.3)
    # A re-answer: the learner's verdict on the earlier answer and their note (design §6.3).
    feedback: Literal["wrong", "incomplete"] | None = None
    feedback_note: str | None = None
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
    history_summary: str | None = None  # ask: the branch's early turns, compressed (§5.3)
    compress_due: bool = False  # ask: turns were dropped that the summary does not cover
    collection: str  # this topic's steward collection name
    all_collections: list[str]  # every active topic's, for the widest scope
    research: list[WebSource] = Field(default_factory=list)  # the topic's, from the outline


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


CHARS_PER_TOKEN = 4  # a rough estimate, enough to keep prompts inside a budget


def estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


class RetrievedSource(BaseModel):
    kind: Literal["entry", "document", "memory"] = "entry"  # only an entry has an entry id
    id: str  # steward's item id (an entry's is its entry id), the `item_id` for feedback
    retrieval_id: str  # the retrieval that returned it, the other half of feedback (design §6.3)
    title: str | None
    content: str

    @classmethod
    def from_item(cls, item: RetrievalItem, retrieval_id: str) -> "RetrievedSource":
        return cls(
            kind=item.kind, id=item.id, retrieval_id=retrieval_id, title=item.title,
            content=item.content,
        )  # fmt: skip


class RetrieveOut(BaseModel):
    status: Literal["ok", "unavailable", "not_configured"]
    retrieval_ids: list[str] = Field(default_factory=list)
    entries: list[RetrievedSource] = Field(default_factory=list)
    memories: list[RetrievedSource] = Field(default_factory=list)


class SufficiencyVerdict(BaseModel):
    """`judge.sufficiency`'s structured output (design §6.2 step 4)."""

    sufficient: bool
    coverage: float = Field(ge=0, le=1)
    missing: list[str] = Field(default_factory=list, max_length=20)


class JudgeOut(BaseModel):
    hit: bool
    skipped_because: str | None = None
    coverage: float | None = None
    missing: list[str] = Field(default_factory=list)


class Citation(BaseModel):
    kind: Literal["kb", "web"]
    entry_id: str | None = None
    item_id: str | None = None
    retrieval_id: str | None = None
    url: str | None = None
    title: str | None = None
    fetched_at: datetime | None = None


class AnswerOut(BaseModel):
    content: str
    source: Source
    roles: list[str]  # role:provider:model of every role that answered (provenance)
    warnings: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    retrieval_ids: list[str] = Field(default_factory=list)  # kb_search's, during the answer
    tools: list[dict[str, str]] = Field(default_factory=list)  # each tool call and its outcome
    valid_to: datetime | None = None  # a web answer's knowledge expires (design §8.2)


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
    jobs: list[JobSpec]

    @property
    def cited_entry_ids(self) -> list[str]:
        """KB entries the answer cites; memories are context, never citations (design §6.2)."""
        return [c.entry_id for c in self.answer.citations if c.kind == "kb" and c.entry_id]


@dataclass(frozen=True)
class AnswerConfig:
    """The budgets and thresholds of one answer (design §6.5), from Settings."""

    top_k: int = 8
    context_budget: int = 3000  # retrieved material, estimated tokens
    judge_threshold: float = 0.7  # ask
    judge_threshold_explain: float = 0.85  # explain: the summary's points must be covered (§5.2)
    answer_max_steps: int = 6  # model requests in one answer loop
    answer_max_tokens: int = 2000  # answer.compose output
    answer_timeout_s: float = 180.0  # the whole loop, searches and fetches included
    answer_loop_tokens: int = 100_000  # the whole loop, input and output, every request
    web_valid_days: int = 90  # web-derived knowledge expires (design §8.2)
    # Steward's collection for KB cards the learner accepted (design §8.5); searched with the topic.
    inbox_collection: str = "inbox"

    def with_inbox(self, collections: list[str]) -> list[str]:
        """Accepted cards are the learner's own knowledge: searched wherever a topic is."""
        return [*collections, self.inbox_collection]


@dataclass
class Deps:
    llm: RoleClient
    steward: StewardReader | None
    emit: Callable[[Event], Awaitable[None]]
    user_id: str
    config: AnswerConfig = field(default_factory=AnswerConfig)
    web: Web | None = None  # None: no search provider configured (design §7.3)
    # Which of these steward entry ids came from a Banyan web answer that has expired (design
    # §8.2: steward cannot hold valid_to yet, so Banyan filters). None: nothing expires.
    expired: Callable[[list[str]], Awaitable[set[str]]] | None = None
    usage: UsageCollector = field(default_factory=UsageCollector)
    # Expired web entries retrieval left out (design §8.2), collected like usage: writeback
    # deprecates them.
    expired_seen: set[str] = field(default_factory=set)
    # The path so far: the orchestrator appends, and a failed or aborted answer is recorded with it.
    path: list[PathEntry] = field(default_factory=list)
