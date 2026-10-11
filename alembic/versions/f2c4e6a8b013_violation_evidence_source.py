"""record where a violation's evidence came from

Officers can now record an offence they didn't see themselves: from a
citizen's video, a social media post or CCTV. `evidence_source` says which
(null means the officer saw it), and `evidence_url` links to the footage or
post so the fine can be traced back to it if it is disputed.

Revision ID: f2c4e6a8b013
Revises: e5b7c9d1f302
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f2c4e6a8b013"
down_revision: Union[str, None] = "e5b7c9d1f302"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.add_column(sa.Column("evidence_source", sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column("evidence_url", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("violations") as batch_op:
        batch_op.drop_column("evidence_url")
        batch_op.drop_column("evidence_source")
