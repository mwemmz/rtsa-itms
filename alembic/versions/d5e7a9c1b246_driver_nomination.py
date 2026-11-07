"""Driver nomination on e-Challans

The owner of a vehicle caught offending with no driver on the spot is asked who was
driving. The named driver then accepts or rejects.

Revision ID: d5e7a9c1b246
Revises: a1c3e5f7b902
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d5e7a9c1b246"
down_revision: Union[str, None] = "a1c3e5f7b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("challans") as b:
        b.add_column(sa.Column("nomination_status", sa.String(20), nullable=False, server_default="none"))
        b.add_column(sa.Column("nomination_deadline", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("nominated_driver_id", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("nominated_by", sa.Uuid(), nullable=True))
        b.add_column(sa.Column("nominated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("nomination_statement", sa.Text(), nullable=True))
        b.add_column(sa.Column("nominee_note", sa.Text(), nullable=True))
        b.create_foreign_key("fk_challans_nominated_driver", "drivers", ["nominated_driver_id"], ["id"])
        b.create_foreign_key("fk_challans_nominated_by", "users", ["nominated_by"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("challans") as b:
        b.drop_constraint("fk_challans_nominated_by", type_="foreignkey")
        b.drop_constraint("fk_challans_nominated_driver", type_="foreignkey")
        for c in ("nominee_note", "nomination_statement", "nominated_at", "nominated_by",
                  "nominated_driver_id", "nomination_deadline", "nomination_status"):
            b.drop_column(c)
