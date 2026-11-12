"""Learning tree: topics, nodes, branches, messages, usage_log, jobs (design §4).

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-12 16:00:44.664310
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column(
            "kind",
            sa.Enum(
                "node_explain",
                "collection_ensure",
                "kb_propose",
                "kb_feedback",
                "kb_deprecate",
                "notes_update",
                "episode_submit",
                "export_render",
                name="job_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "done",
                "failed",
                "dead",
                name="job_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=200), nullable=True),
        sa.Column("dedupe_key", sa.String(length=200), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "run_after"], unique=False)
    op.create_index(
        "uq_jobs_pending_dedupe",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'pending' AND dedupe_key IS NOT NULL"),
    )
    op.create_table(
        "topics",
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("goal", sa.Text(), nullable=True),
        sa.Column(
            "level",
            sa.Enum(
                "beginner",
                "intermediate",
                "advanced",
                name="topic_level",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "language",
            sa.Enum(
                "zh",
                "en",
                "mixed",
                name="topic_language",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("instructions", sa.Text(), nullable=True),
        sa.Column("steward_collection_id", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "archived",
                name="topic_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "outline_status",
            sa.Enum(
                "draft",
                "confirmed",
                name="outline_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "explain_mode",
            sa.Enum(
                "all",
                "prefetch",
                "manual",
                name="explain_mode",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("research_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "nodes",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "not_started",
                "learning",
                "done",
                name="node_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "origin",
            sa.Enum(
                "outline",
                "user",
                "prerequisite",
                "question",
                name="node_origin",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("depth BETWEEN 0 AND 2", name="node_depth"),
        sa.ForeignKeyConstraint(["parent_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_nodes_topic_parent_position",
        "nodes",
        ["topic_id", "parent_id", "position"],
        unique=False,
    )
    op.create_table(
        "branches",
        sa.Column("node_id", sa.Uuid(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "main",
                "question",
                name="branch_kind",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("episode_job_id", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["node_id"], ["nodes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_branches_one_main_per_node",
        "branches",
        ["node_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'main'"),
    )
    op.create_table(
        "messages",
        sa.Column("branch_id", sa.Uuid(), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "user",
                "assistant",
                name="message_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "streaming",
                "done",
                "aborted",
                "failed",
                name="message_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Enum(
                "kb",
                "llm",
                "web",
                "mixed",
                name="message_source",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("path", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("provenance", sa.Text(), nullable=True),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("steward_retrieval_ids", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("cited_entry_ids", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column("proposed_entry_ids", sa.ARRAY(sa.Text()), nullable=False),
        sa.Column(
            "feedback",
            sa.Enum(
                "none",
                "good",
                "wrong",
                "incomplete",
                name="message_feedback",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("feedback_note", sa.Text(), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["superseded_by"], ["messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_messages_branch_created", "messages", ["branch_id", "created_at"], unique=False
    )
    op.create_index(
        "uq_messages_one_streaming_per_branch",
        "messages",
        ["branch_id"],
        unique=True,
        postgresql_where=sa.text("status = 'streaming'"),
    )
    op.create_table(
        "usage_log",
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("generation_run_id", sa.Uuid(), nullable=True),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("path_kind", sa.String(length=32), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column(
            "error_kind",
            sa.Enum(
                "timeout",
                "provider",
                "validation",
                "budget",
                "aborted",
                name="usage_error",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["message_id"], ["messages.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_usage_log_topic_created", "usage_log", ["topic_id", "created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_usage_log_topic_created", table_name="usage_log")
    op.drop_table("usage_log")
    op.drop_index(
        "uq_messages_one_streaming_per_branch",
        table_name="messages",
        postgresql_where=sa.text("status = 'streaming'"),
    )
    op.drop_index("ix_messages_branch_created", table_name="messages")
    op.drop_table("messages")
    op.drop_index(
        "uq_branches_one_main_per_node",
        table_name="branches",
        postgresql_where=sa.text("kind = 'main'"),
    )
    op.drop_table("branches")
    op.drop_index("ix_nodes_topic_parent_position", table_name="nodes")
    op.drop_table("nodes")
    op.drop_table("topics")
    op.drop_index(
        "uq_jobs_pending_dedupe",
        table_name="jobs",
        postgresql_where=sa.text("status = 'pending' AND dedupe_key IS NOT NULL"),
    )
    op.drop_index("ix_jobs_claim", table_name="jobs")
    op.drop_table("jobs")
