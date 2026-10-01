"""link accidents to the road network

An accident used to carry only free-text `location`, so the road incident it
raised had no road or segment: the alert feed could not say where it was, the
road status board never showed the road as closed and the route planner never
avoided it. Accidents now point at the road and stretch (segment) they happened
on, and at the road incident they put on the live feed so it can be cleared.

Revision ID: b3f5a7c9d012
Revises: d2e4f6a8b901
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b3f5a7c9d012"
down_revision: Union[str, None] = "d2e4f6a8b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("accidents") as batch_op:
        batch_op.add_column(sa.Column("road_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("segment_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("incident_id", sa.Uuid(), nullable=True))
        batch_op.create_index(op.f("ix_accidents_road_id"), ["road_id"], unique=False)
        batch_op.create_index(op.f("ix_accidents_segment_id"), ["segment_id"], unique=False)
        batch_op.create_foreign_key("fk_accidents_road_id", "roads", ["road_id"], ["id"])
        batch_op.create_foreign_key("fk_accidents_segment_id", "road_segments", ["segment_id"], ["id"])
        batch_op.create_foreign_key("fk_accidents_incident_id", "road_incidents", ["incident_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("accidents") as batch_op:
        batch_op.drop_constraint("fk_accidents_incident_id", type_="foreignkey")
        batch_op.drop_constraint("fk_accidents_segment_id", type_="foreignkey")
        batch_op.drop_constraint("fk_accidents_road_id", type_="foreignkey")
        batch_op.drop_index(op.f("ix_accidents_segment_id"))
        batch_op.drop_index(op.f("ix_accidents_road_id"))
        batch_op.drop_column("incident_id")
        batch_op.drop_column("segment_id")
        batch_op.drop_column("road_id")
