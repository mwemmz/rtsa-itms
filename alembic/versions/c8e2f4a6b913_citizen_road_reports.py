"""citizen road reports, NRC on accounts and false-report fines

Citizens can now report road incidents. Their reports go on the live feed and
close the stretch in the route planner straight away, marked ``unverified`` until
an officer confirms, dismisses or rejects them as false. To make a false report
answerable, a citizen signs up with their NRC number, and rejecting a report as
false fines the reporter's account - so challans (and their violations) can now
be charged to a user rather than only to a vehicle or driver.

Revision ID: c8e2f4a6b913
Revises: b3f5a7c9d012
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c8e2f4a6b913"
down_revision: Union[str, None] = "b3f5a7c9d012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # Same approach as d2e4f6a8b901: ADD VALUE on its own autocommit
        # connection, because the migration connection already has a transaction
        # open. SQLite stores enums as VARCHAR and needs no DDL.
        with bind.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(sa.text("ALTER TYPE violationtype ADD VALUE IF NOT EXISTS 'FALSE_REPORT'"))

    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("nrc_number", sa.String(length=20), nullable=True))
        batch_op.create_index(op.f("ix_users_nrc_number"), ["nrc_number"], unique=True)

    with op.batch_alter_table("road_incidents") as batch_op:
        batch_op.add_column(
            sa.Column("verification", sa.String(length=12), nullable=False, server_default="official")
        )
        batch_op.add_column(sa.Column("reviewed_by", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("review_note", sa.Text(), nullable=True))
        batch_op.create_index(op.f("ix_road_incidents_verification"), ["verification"], unique=False)
        batch_op.create_foreign_key("fk_road_incidents_reviewed_by", "users", ["reviewed_by"], ["id"])

    for table in ("violations", "challans"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column("user_id", sa.Uuid(), nullable=True))
            batch_op.create_index(op.f(f"ix_{table}_user_id"), ["user_id"], unique=False)
            batch_op.create_foreign_key(f"fk_{table}_user_id", "users", ["user_id"], ["id"])


def downgrade() -> None:
    for table in ("challans", "violations"):
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_constraint(f"fk_{table}_user_id", type_="foreignkey")
            batch_op.drop_index(op.f(f"ix_{table}_user_id"))
            batch_op.drop_column("user_id")

    with op.batch_alter_table("road_incidents") as batch_op:
        batch_op.drop_constraint("fk_road_incidents_reviewed_by", type_="foreignkey")
        batch_op.drop_index(op.f("ix_road_incidents_verification"))
        batch_op.drop_column("review_note")
        batch_op.drop_column("reviewed_at")
        batch_op.drop_column("reviewed_by")
        batch_op.drop_column("verification")

    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_index(op.f("ix_users_nrc_number"))
        batch_op.drop_column("nrc_number")
    # The FALSE_REPORT enum label stays: PostgreSQL can't drop one in place.
