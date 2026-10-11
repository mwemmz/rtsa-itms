"""photos as evidence on a violation

Officers can attach photos to a violation: the scene, the plate, a frame of a
citizen's video. The image itself is stored in the database (`data`), because
the app's host doesn't keep files on disk across deploys. `sha256` is taken on
upload so the evidence can be shown to be unaltered if the fine is disputed.

Revision ID: a3d5f7b9c124
Revises: f2c4e6a8b013
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a3d5f7b9c124"
down_revision: Union[str, None] = "f2c4e6a8b013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "violation_photos",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("violation_id", sa.Uuid(), nullable=False),
        sa.Column("content_type", sa.String(length=30), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("uploaded_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["violation_id"], ["violations.id"]),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_violation_photos_violation_id", "violation_photos", ["violation_id"])


def downgrade() -> None:
    op.drop_index("ix_violation_photos_violation_id", table_name="violation_photos")
    op.drop_table("violation_photos")
