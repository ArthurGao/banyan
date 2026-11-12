"""Branch compression (design §5.3): a branch's summary of its early turns and the job kind that
writes it.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-13 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS = (
    "node_explain", "collection_ensure", "kb_propose", "kb_feedback", "kb_deprecate",
    "notes_update", "episode_submit", "export_render",
)  # fmt: skip


def _kinds(kinds: tuple[str, ...]) -> None:
    op.drop_constraint("job_kind", "jobs", type_="check")
    listed = ", ".join(f"'{k}'" for k in kinds)
    op.create_check_constraint("job_kind", "jobs", f"kind IN ({listed})")


def upgrade() -> None:
    op.add_column("branches", sa.Column("summary_md", sa.Text(), nullable=True))
    op.add_column("branches", sa.Column("summary_through", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "branches_summary_through_fkey", "branches", "messages", ["summary_through"], ["id"],
        ondelete="SET NULL",
    )  # fmt: skip
    _kinds((*KINDS, "branch_compress"))


def downgrade() -> None:
    op.execute("DELETE FROM jobs WHERE kind = 'branch_compress'")
    _kinds(KINDS)
    op.drop_constraint("branches_summary_through_fkey", "branches", type_="foreignkey")
    op.drop_column("branches", "summary_through")
    op.drop_column("branches", "summary_md")
