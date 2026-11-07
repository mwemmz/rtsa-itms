"""Policy types and premium on insurance; checklist and readings on inspections

Revision ID: f1b2c3d4e5a6
Revises: e8a1c3d5f702
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1b2c3d4e5a6"
down_revision: Union[str, None] = "e8a1c3d5f702"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("insurance", sa.Column("policy_type", sa.String(40), nullable=False, server_default="third_party"))
    op.add_column("insurance", sa.Column("premium", sa.Integer(), nullable=True))
    op.add_column("inspections", sa.Column("checklist", sa.Text(), nullable=True))
    op.add_column("inspections", sa.Column("readings", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("inspections", "readings")
    op.drop_column("inspections", "checklist")
    op.drop_column("insurance", "premium")
    op.drop_column("insurance", "policy_type")
