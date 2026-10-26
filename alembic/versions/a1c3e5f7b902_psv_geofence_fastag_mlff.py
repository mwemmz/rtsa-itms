"""PSV geo-fencing, FASTag identification and free-flow (MLFF) tolling

* `psv_corridors`: the approved route of a permit (waypoints plus an allowed distance).
* `psv_route_alerts`: raised when a PSV is reported outside its corridor.
* `vehicles.fastag_id`: the FASTag / transponder id, unique.
* `toll_transactions.identification_method` and `lane_mode`: how the vehicle was
  identified and whether it passed a barrier lane or a free-flow lane.

Revision ID: a1c3e5f7b902
Revises: b6d8f0a2c335
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1c3e5f7b902"
down_revision: Union[str, None] = "b6d8f0a2c335"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "psv_corridors",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("permit_id", sa.Uuid(), nullable=False),
        sa.Column("waypoints", sa.Text(), nullable=False),
        sa.Column("buffer_m", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["permit_id"], ["psv_permits.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("permit_id"),
    )
    op.create_table(
        "psv_route_alerts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("permit_id", sa.Uuid(), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("distance_m", sa.Integer(), nullable=False),
        sa.Column("buffer_m", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("acknowledged_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["permit_id"], ["psv_permits.id"]),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        sa.ForeignKeyConstraint(["acknowledged_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_psv_route_alerts_permit_id", "psv_route_alerts", ["permit_id"])
    op.create_index("ix_psv_route_alerts_vehicle_id", "psv_route_alerts", ["vehicle_id"])
    op.create_index("ix_psv_route_alerts_created_at", "psv_route_alerts", ["created_at"])

    with op.batch_alter_table("vehicles") as batch_op:
        batch_op.add_column(sa.Column("fastag_id", sa.String(length=64), nullable=True))
    op.create_index("ix_vehicles_fastag_id", "vehicles", ["fastag_id"], unique=True)

    with op.batch_alter_table("toll_transactions") as batch_op:
        batch_op.add_column(
            sa.Column("identification_method", sa.String(length=20), nullable=False, server_default="anpr")
        )
        batch_op.add_column(sa.Column("lane_mode", sa.String(length=20), nullable=False, server_default="barrier"))


def downgrade() -> None:
    with op.batch_alter_table("toll_transactions") as batch_op:
        batch_op.drop_column("lane_mode")
        batch_op.drop_column("identification_method")
    op.drop_index("ix_vehicles_fastag_id", table_name="vehicles")
    with op.batch_alter_table("vehicles") as batch_op:
        batch_op.drop_column("fastag_id")
    op.drop_index("ix_psv_route_alerts_created_at", table_name="psv_route_alerts")
    op.drop_index("ix_psv_route_alerts_vehicle_id", table_name="psv_route_alerts")
    op.drop_index("ix_psv_route_alerts_permit_id", table_name="psv_route_alerts")
    op.drop_table("psv_route_alerts")
    op.drop_table("psv_corridors")
