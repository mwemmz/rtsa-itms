"""OBD-II fault codes on inspections

Revision ID: a7c9e1b3d5f8
Revises: f1b2c3d4e5a6
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7c9e1b3d5f8"
down_revision: Union[str, None] = "f1b2c3d4e5a6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("inspections", sa.Column("obd_codes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("inspections", "obd_codes")
