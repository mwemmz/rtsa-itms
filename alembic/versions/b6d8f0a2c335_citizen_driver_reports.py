"""citizens report dangerous driving, with photo or video evidence

`driver_reports` holds a citizen's report of someone else's driving: the plate,
what they saw, where and when, and a link to a video. Photos sent with it go in
`violation_photos`, which gains `report_id` and lets `violation_id` be empty
until an officer accepts the report and the photos move to the new violation.

Revision ID: b6d8f0a2c335
Revises: a3d5f7b9c124
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b6d8f0a2c335"
down_revision: Union[str, None] = "a3d5f7b9c124"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "driver_reports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=False),
        sa.Column("plate_number", sa.String(length=20), nullable=False),
        sa.Column("vehicle_id", sa.Uuid(), nullable=True),
        sa.Column("violation_type", sa.String(length=30), nullable=False),
        sa.Column("location", sa.String(length=200), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("gps_accuracy_m", sa.Float(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("evidence_url", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("violation_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["vehicle_id"], ["vehicles.id"]),
        sa.ForeignKeyConstraint(["reviewed_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["violation_id"], ["violations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_driver_reports_reporter_id", "driver_reports", ["reporter_id"])
    op.create_index("ix_driver_reports_plate_number", "driver_reports", ["plate_number"])
    op.create_index("ix_driver_reports_status", "driver_reports", ["status"])

    with op.batch_alter_table("violation_photos") as batch_op:
        batch_op.alter_column("violation_id", existing_type=sa.Uuid(), nullable=True)
        batch_op.add_column(sa.Column("report_id", sa.Uuid(), nullable=True))
        batch_op.create_foreign_key("fk_violation_photos_report_id", "driver_reports", ["report_id"], ["id"])
        batch_op.create_index("ix_violation_photos_report_id", ["report_id"])


def downgrade() -> None:
    with op.batch_alter_table("violation_photos") as batch_op:
        batch_op.drop_index("ix_violation_photos_report_id")
        batch_op.drop_constraint("fk_violation_photos_report_id", type_="foreignkey")
        batch_op.drop_column("report_id")
        batch_op.alter_column("violation_id", existing_type=sa.Uuid(), nullable=False)
    op.drop_index("ix_driver_reports_status", table_name="driver_reports")
    op.drop_index("ix_driver_reports_plate_number", table_name="driver_reports")
    op.drop_index("ix_driver_reports_reporter_id", table_name="driver_reports")
    op.drop_table("driver_reports")
