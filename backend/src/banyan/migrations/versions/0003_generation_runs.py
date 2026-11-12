"""Generation runs ("generate all", design §5.1.1) and usage_log.generation_run_id.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-12 16:40:35.177554
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FK = "usage_log_generation_run_id_fkey"


def upgrade() -> None:
    op.create_table(
        "generation_runs",
        sa.Column("topic_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "paused",
                "budget_exhausted",
                "cancelled",
                "done",
                name="run_status",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("node_ids", sa.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("estimate", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_generation_runs_one_active_per_topic",
        "generation_runs",
        ["topic_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('running', 'paused', 'budget_exhausted')"),
    )
    op.create_index(
        "ix_usage_log_generation_run",
        "usage_log",
        ["generation_run_id"],
        postgresql_where=sa.text("generation_run_id IS NOT NULL"),
    )
    op.create_foreign_key(
        FK, "usage_log", "generation_runs", ["generation_run_id"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    op.drop_constraint(FK, "usage_log", type_="foreignkey")
    op.drop_index("ix_usage_log_generation_run", table_name="usage_log")
    op.drop_index(
        "uq_generation_runs_one_active_per_topic",
        table_name="generation_runs",
        postgresql_where=sa.text("status IN ('running', 'paused', 'budget_exhausted')"),
    )
    op.drop_table("generation_runs")
