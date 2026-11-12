"""The topic's latest generation run, finished or not, for GET /topics/{id} (`latest_run_id`).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-13 18:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_generation_runs_topic_created", "generation_runs", ["topic_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_generation_runs_topic_created", table_name="generation_runs")
