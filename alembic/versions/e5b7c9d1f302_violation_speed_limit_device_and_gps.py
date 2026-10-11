"""record the speed limit, speed device and GPS fix on a violation

A speeding ticket only carried the measured speed. A speed on its own doesn't
show an offence: the ticket also needs the posted limit it was over and the
radar or camera that took the reading, or it falls apart the moment it is
disputed. `speed_limit_kmh` and `speed_device` hold those; the API requires the
limit on a speeding violation and checks the speed is actually over it.

The place was free text only ("Great East Road, near the flyover"), which can't
be put on a map. `latitude`, `longitude` and `gps_accuracy_m` hold the fix the
officer's phone reported when the violation was recorded. All are nullable:
older violations have none, and GPS is not always available.

Revision ID: e5b7c9d1f302
Revises: d9f1a3b5c724
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e5b7c9d1f302"
down_revision: Union[str, None] = "d9f1a3b5c724"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.add_column(sa.Column("speed_limit_kmh", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("speed_device", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("latitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("longitude", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("gps_accuracy_m", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.drop_column("gps_accuracy_m")
        batch_op.drop_column("longitude")
        batch_op.drop_column("latitude")
        batch_op.drop_column("speed_device")
        batch_op.drop_column("speed_limit_kmh")
