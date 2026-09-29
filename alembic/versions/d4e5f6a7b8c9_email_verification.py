"""email verification: users.email_verified_at

Existing accounts are treated as confirmed (backfilled with their creation time),
so nobody who already uses the system loses email notifications. Only accounts
that sign themselves up from now on start unconfirmed.

Revision ID: d4e5f6a7b8c9
Revises: c1b6d7e8f901
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, tuple[str, str], None] = "c1b6d7e8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET email_verified_at = COALESCE(created_at, CURRENT_TIMESTAMP)")


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
