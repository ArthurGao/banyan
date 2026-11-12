"""Request and response bodies of `/v1` (the frontend's contract, design §9)."""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from banyan.models import (
    BranchKind,
    ExplainMode,
    ExportStatus,
    Feedback,
    Language,
    Level,
    MessageRole,
    MessageStatus,
    NodeOrigin,
    NodeStatus,
    OutlineStatus,
    RunStatus,
    Source,
    TopicStatus,
)
from banyan.pipeline.events import PathEntry
from banyan.services.notes import NotesContent


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=2000)
    level: Level = Level.BEGINNER
    language: Language = Language.ZH
    instructions: str | None = Field(default=None, max_length=10_000)


class TopicPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=2000)
    level: Level | None = None
    language: Language | None = None
    instructions: str | None = Field(default=None, max_length=10_000)
    status: TopicStatus | None = None
    explain_mode: ExplainMode | None = Field(
        default=None, description="Can change at any time after confirming (design §5.1 step 5)"
    )


class TopicOut(_Out):
    id: uuid.UUID
    title: str
    goal: str | None
    level: Level
    language: Language
    instructions: str | None
    status: TopicStatus
    outline_status: OutlineStatus
    explain_mode: ExplainMode
    created_at: datetime


class TopicSummary(TopicOut):
    nodes: int
    done: int


class NodeOut(_Out):
    id: uuid.UUID
    parent_id: uuid.UUID | None
    position: int
    depth: int
    title: str
    summary: str | None
    status: NodeStatus
    origin: NodeOrigin


class TopicDetail(BaseModel):
    topic: TopicOut
    nodes: list[NodeOut] = Field(
        description="The outline, flat: parent_id and position give the tree"
    )
    latest_run_id: uuid.UUID | None = Field(
        default=None, description="The topic's most recent generation run, finished or not"
    )


class ConfirmOutline(BaseModel):
    explain_mode: ExplainMode = ExplainMode.MANUAL


class NodeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    parent_id: uuid.UUID | None = None
    position: int | None = Field(default=None, ge=0, description="Default: last among siblings")
    origin: NodeOrigin = NodeOrigin.USER


class NodePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    summary: str | None = Field(default=None, max_length=2000)
    status: NodeStatus | None = None


class NodeMove(BaseModel):
    parent_id: uuid.UUID | None
    position: int = Field(ge=0)


class NodeDeleted(BaseModel):
    proposed_entry_ids: list[str] = Field(
        description="KB entries proposed by this node's answers; kept in steward unless deprecated"
    )


class BranchCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class BranchPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    node_id: uuid.UUID | None = Field(default=None, description="Move the branch to another node")
    closed: bool | None = None


class BranchOut(_Out):
    id: uuid.UUID
    node_id: uuid.UUID
    kind: BranchKind
    title: str
    closed_at: datetime | None
    created_at: datetime


class MessageOut(_Out):
    id: uuid.UUID
    branch_id: uuid.UUID
    role: MessageRole
    status: MessageStatus
    content_md: str
    source: Source | None
    path: list[PathEntry]
    provenance: str | None
    citations: list[dict[str, Any]]
    feedback: Feedback
    superseded_by: uuid.UUID | None
    created_at: datetime


class NodeDetail(BaseModel):
    node: NodeOut
    explanation: MessageOut | None
    explanation_status: Literal["none", "generating", "done", "failed"] = Field(
        description="generating: a background job or another stream is making it (design §5.1.1)"
    )
    branches: list[BranchOut]


class BranchDetail(BaseModel):
    branch: BranchOut
    messages: list[MessageOut] = Field(description="Committed messages only (status done)")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    allow_web: bool = Field(
        default=True,
        description="False: no web tools (B4); a time-sensitive answer gets warnings=[stale_risk]",
    )


class EstimateOut(BaseModel):
    nodes: int = Field(description="Nodes still without an explanation")
    tokens_per_node: int
    total: int
    basis: Literal["history", "default"] = Field(
        description="history: median of the last 50 explanations; default: configured guess"
    )


class RunCreate(BaseModel):
    token_budget: int | None = Field(
        default=None, gt=0, description="Default: BANYAN_BULK_MAX_TOKENS"
    )


class RunPatch(BaseModel):
    action: Literal["pause", "resume", "cancel"] | None = None
    add_budget: int | None = Field(default=None, gt=0)


class RunNode(BaseModel):
    node_id: uuid.UUID
    status: Literal["none", "generating", "done", "failed"]


class RunOut(BaseModel):
    id: uuid.UUID
    topic_id: uuid.UUID
    status: RunStatus
    token_budget: int
    tokens_used: int
    estimate: EstimateOut
    nodes: list[RunNode] = Field(description="In outline order")
    created_at: datetime


class NotesOut(BaseModel):
    node_id: uuid.UUID
    version: int = Field(description="0: no notes yet")
    content: NotesContent | None
    updated_at: datetime | None


class ExportOut(_Out):
    id: uuid.UUID
    topic_id: uuid.UUID
    status: ExportStatus
    error: str | None
    notes_versions: dict[str, int] = Field(description="node id -> notes version used")
    created_at: datetime


class FeedbackIn(BaseModel):
    feedback: Literal["good", "wrong", "incomplete"]
    note: str | None = Field(default=None, max_length=2000)
    reanswer: bool = Field(
        default=True, description="wrong/incomplete: stream a new answer (SSE) instead of JSON"
    )


class TopicUsageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    topic_id: uuid.UUID
    title: str
    answers: int
    kb_answers: int
    hit_rate: float = Field(description="Share of committed answers that came from the KB")
    tokens_by_role: dict[str, int]
    estimated_tokens_saved: int = Field(
        description="KB answers times this topic's average strong-model answer (design §11.2)"
    )


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content: str
    memory_type: str
    pinned: bool = Field(description="Always apply: put in every answer's context (design §8.5)")
    created_at: datetime
    updated_at: datetime


class MemoryPageOut(BaseModel):
    items: list[MemoryOut]
    next_cursor: str | None


class MemoryPatch(BaseModel):
    pinned: bool


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    content: str
    reason: str | None
    risk_level: Literal["low", "medium", "high"]
    created_at: datetime
    expires_at: datetime | None


class CandidatePageOut(BaseModel):
    items: list[CandidateOut]
    next_cursor: str | None


class CandidateDecision(BaseModel):
    decision: Literal["accept", "reject"] = Field(
        description="accept: the card becomes a knowledge base entry"
    )
    note: str | None = Field(default=None, max_length=2000)


class ClassifyIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class NewNodeIn(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(default="", max_length=2000)
    parent_id: uuid.UUID | None = None
    position: int = Field(default=0, ge=0)


class ClassifyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    node_id: uuid.UUID | None = Field(description="The node the question belongs to, if any")
    confidence: float
    confident: bool = Field(description="False: ask the learner (offer suggested_node)")
    suggested_node: NewNodeIn | None = Field(
        description="When not confident: a node the outline lacks, to add if the learner agrees"
    )


class QuickAskIn(AskBody):
    node_id: uuid.UUID | None = Field(default=None, description="Ask in this node")
    new_node: NewNodeIn | None = Field(
        default=None, description="Or add this node (origin=question) and ask there"
    )

    @model_validator(mode="after")
    def _one_place(self) -> Self:
        if (self.node_id is None) == (self.new_node is None):
            raise ValueError("give exactly one of node_id and new_node")
        return self
