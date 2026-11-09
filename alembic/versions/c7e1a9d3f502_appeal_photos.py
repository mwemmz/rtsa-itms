"""Photo evidence on appeals

Revision ID: c7e1a9d3f502
Revises: b3d5f7a9c1e2
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c7e1a9d3f502"
down_revision: Union[str, None] = "b3d5f7a9c1e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("violation_photos") as b:
        b.add_column(sa.Column("appeal_id", sa.Uuid(), nullable=True))
        b.create_foreign_key("fk_violation_photos_appeal", "appeals", ["appeal_id"], ["id"])
        b.create_index("ix_violation_photos_appeal_id", ["appeal_id"])


def downgrade() -> None:
    with op.batch_alter_table("violation_photos") as b:
        b.drop_index("ix_violation_photos_appeal_id")
        b.drop_constraint("fk_violation_photos_appeal", type_="foreignkey")
        b.drop_column("appeal_id")
