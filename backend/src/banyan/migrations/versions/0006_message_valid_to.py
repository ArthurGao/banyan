"""Web answers expire: messages.valid_to (design §8.2).

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-12 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True))
    # Only web answers have one; retrieval asks which of them have passed.
    op.create_index(
        "ix_messages_valid_to",
        "messages",
        ["valid_to"],
        postgresql_where=sa.text("valid_to IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_messages_valid_to", table_name="messages")
    op.drop_column("messages", "valid_to")
