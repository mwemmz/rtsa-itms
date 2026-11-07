"""Vehicle registration expiry and licence application fee paid-at

Revision ID: b3d5f7a9c1e2
Revises: a7c9e1b3d5f8
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3d5f7a9c1e2"
down_revision: Union[str, None] = "a7c9e1b3d5f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("vehicles", sa.Column("registration_expiry", sa.DateTime(timezone=True), nullable=True))
    op.add_column("licence_applications", sa.Column("fee_paid_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("licence_applications", "fee_paid_at")
    op.drop_column("vehicles", "registration_expiry")
