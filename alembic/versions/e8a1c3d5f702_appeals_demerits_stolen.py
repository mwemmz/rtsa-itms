"""Appeals, demerit points and stolen-vehicle reports

* `appeals`: a citizen contesting an e-Challan.
* `demerit_points`: points a driver earned per violation (voided if the fine is cancelled).
* `vehicles`: theft report fields.
* `challanstatus` gains CANCELLED (waived).

Revision ID: e8a1c3d5f702
Revises: d5e7a9c1b246
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e8a1c3d5f702"
down_revision: Union[str, None] = "d5e7a9c1b246"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE challanstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")

    op.create_table(
        "appeals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("challan_id", sa.Uuid(), nullable=False),
        sa.Column("appellant_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("details", sa.Text(), nullable=False),
        sa.Column("prior_status", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=True),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["challan_id"], ["challans.id"]),
        sa.ForeignKeyConstraint(["appellant_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_appeals_challan_id", "appeals", ["challan_id"])
    op.create_index("ix_appeals_appellant_id", "appeals", ["appellant_id"])

    op.create_table(
        "demerit_points",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("driver_id", sa.Uuid(), nullable=False),
        sa.Column("violation_id", sa.Uuid(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(100), nullable=False),
        sa.Column("awarded_at", sa.DateTime(), nullable=False),
        sa.Column("voided_at", sa.DateTime(), nullable=True),
        sa.Column("voided_reason", sa.String(200), nullable=True),
        sa.ForeignKeyConstraint(["driver_id"], ["drivers.id"]),
        sa.ForeignKeyConstraint(["violation_id"], ["violations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("violation_id"),
    )
    op.create_index("ix_demerit_points_driver_id", "demerit_points", ["driver_id"])

    with op.batch_alter_table("vehicles") as b:
        b.add_column(sa.Column("stolen_reported_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("police_report_number", sa.String(60), nullable=True))
        b.add_column(sa.Column("recovered_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("vehicles") as b:
        b.drop_column("recovered_at")
        b.drop_column("police_report_number")
        b.drop_column("stolen_reported_at")
    op.drop_index("ix_demerit_points_driver_id", table_name="demerit_points")
    op.drop_table("demerit_points")
    op.drop_index("ix_appeals_appellant_id", table_name="appeals")
    op.drop_index("ix_appeals_challan_id", table_name="appeals")
    op.drop_table("appeals")
    # the CANCELLED enum value is left in place: Postgres cannot drop a value from an enum
