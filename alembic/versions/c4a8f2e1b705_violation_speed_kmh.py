"""record the observed road speed on a violation

A violation only carried free-text `location` and `description`, so there was
nowhere to put what the driver was actually doing when they were caught. For a
speeding offence that is the single most important fact on the ticket, and for
reckless or drunk driving it is the supporting evidence. The reading was being
written into the description prose by hand, which is not queryable and cannot be
reported on.

`speed_kmh` is a nullable float so radar and camera readings keep their decimal
precision (87.5, not a rounded 88). It stays optional on the column because most
offences have no meaningful speed reading; the API and form prompt for it when
the violation type is one where it matters.

Revision ID: c4a8f2e1b705
Revises: b3f5a7c9d012
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c4a8f2e1b705"
down_revision: Union[str, None] = "b3f5a7c9d012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.add_column(sa.Column("speed_kmh", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.drop_column("speed_kmh")