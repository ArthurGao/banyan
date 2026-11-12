"""The learning tree, messages, usage and jobs (design §4). B2 adds generation_runs, node_notes and
exports."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from banyan.models.base import Base, enum_column
from banyan.models.enums import (
    BranchKind,
    ErrorKind,
    ExplainMode,
    Feedback,
    JobKind,
    JobStatus,
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

MAX_DEPTH = 2  # nodes are at most three levels: depth 0..2


class Topic(Base):
    __tablename__ = "topics"

    title: Mapped[str] = mapped_column(String(200))
    goal: Mapped[str | None] = mapped_column(Text)
    level: Mapped[Level] = mapped_column(enum_column(Level, "topic_level"), default=Level.BEGINNER)
    language: Mapped[Language] = mapped_column(
        enum_column(Language, "topic_language"), default=Language.ZH
    )
    instructions: Mapped[str | None] = mapped_column(Text)
    steward_collection_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[TopicStatus] = mapped_column(
        enum_column(TopicStatus, "topic_status"), default=TopicStatus.ACTIVE
    )
    outline_status: Mapped[OutlineStatus] = mapped_column(
        enum_column(OutlineStatus, "outline_status"), default=OutlineStatus.DRAFT
    )
    explain_mode: Mapped[ExplainMode] = mapped_column(
        enum_column(ExplainMode, "explain_mode"), default=ExplainMode.MANUAL
    )
    research_sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (
        CheckConstraint(f"depth BETWEEN 0 AND {MAX_DEPTH}", name="node_depth"),
        Index("ix_nodes_topic_parent_position", "topic_id", "parent_id", "position"),
    )

    topic_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("topics.id", ondelete="CASCADE"))
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("nodes.id", ondelete="CASCADE")
    )
    position: Mapped[int] = mapped_column(Integer)
    depth: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[NodeStatus] = mapped_column(
        enum_column(NodeStatus, "node_status"), default=NodeStatus.NOT_STARTED
    )
    origin: Mapped[NodeOrigin] = mapped_column(
        enum_column(NodeOrigin, "node_origin"), default=NodeOrigin.OUTLINE
    )


class Branch(Base):
    __tablename__ = "branches"
    __table_args__ = (
        # At most one main branch (the explanation) per node (design §4).
        Index(
            "uq_branches_one_main_per_node",
            "node_id",
            unique=True,
            postgresql_where=text("kind = 'main'"),
        ),
    )

    node_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("nodes.id", ondelete="CASCADE"))
    kind: Mapped[BranchKind] = mapped_column(enum_column(BranchKind, "branch_kind"))
    title: Mapped[str] = mapped_column(String(300))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    episode_job_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # One answer streams per branch at a time; a second POST is a 409 (design §9).
        Index(
            "uq_messages_one_streaming_per_branch",
            "branch_id",
            unique=True,
            postgresql_where=text("status = 'streaming'"),
        ),
        Index("ix_messages_branch_created", "branch_id", "created_at"),
    )

    branch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("branches.id", ondelete="CASCADE")
    )
    role: Mapped[MessageRole] = mapped_column(enum_column(MessageRole, "message_role"))
    status: Mapped[MessageStatus] = mapped_column(enum_column(MessageStatus, "message_status"))
    content_md: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[Source | None] = mapped_column(enum_column(Source, "message_source"))
    path: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    provenance: Mapped[str | None] = mapped_column(Text)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    steward_retrieval_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    cited_entry_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    proposed_entry_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    feedback: Mapped[Feedback] = mapped_column(
        enum_column(Feedback, "message_feedback"), default=Feedback.NONE
    )
    feedback_note: Mapped[str | None] = mapped_column(Text)
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )


class UsageLog(Base):
    """One row per LLM provider attempt, failed and retried ones included (design §4, §7.2)."""

    __tablename__ = "usage_log"
    __table_args__ = (Index("ix_usage_log_topic_created", "topic_id", "created_at"),)

    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="SET NULL")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("messages.id", ondelete="SET NULL")
    )
    generation_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)  # FK arrives with B2
    role: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    attempt: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    path_kind: Mapped[str | None] = mapped_column(String(32))
    ok: Mapped[bool] = mapped_column(Boolean)
    error_kind: Mapped[ErrorKind | None] = mapped_column(enum_column(ErrorKind, "usage_error"))
    latency_ms: Mapped[int] = mapped_column(Integer)


class Job(Base):
    """Outbox of writes to steward and background work (design §4). Written in B1, run from B2."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_claim", "status", "run_after"),
        # Only one pending job per dedupe key (notes_update per node, design §4).
        Index(
            "uq_jobs_pending_dedupe",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'pending' AND dedupe_key IS NOT NULL"),
        ),
    )

    kind: Mapped[JobKind] = mapped_column(enum_column(JobKind, "job_kind"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    status: Mapped[JobStatus] = mapped_column(
        enum_column(JobStatus, "job_status"), default=JobStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(200))
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
