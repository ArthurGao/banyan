"""Request and response bodies of `/v1` (the frontend's contract, design §9)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from banyan.models import (
    BranchKind,
    ExplainMode,
    Feedback,
    Language,
    Level,
    MessageRole,
    MessageStatus,
    NodeOrigin,
    NodeStatus,
    OutlineStatus,
    Source,
    TopicStatus,
)
from banyan.pipeline.events import PathEntry


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
