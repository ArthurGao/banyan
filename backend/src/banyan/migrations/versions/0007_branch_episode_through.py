"""Episodes: a branch's watermark of what steward has (design §8.5); the unused episode_job_id goes.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-12 21:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("branches", "episode_job_id")
    op.add_column("branches", sa.Column("episode_through", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "branches_episode_through_fkey", "branches", "messages", ["episode_through"], ["id"],
        ondelete="SET NULL",
    )  # fmt: skip


def downgrade() -> None:
    op.drop_constraint("branches_episode_through_fkey", "branches", type_="foreignkey")
    op.drop_column("branches", "episode_through")
    op.add_column("branches", sa.Column("episode_job_id", sa.Uuid(), nullable=True))
