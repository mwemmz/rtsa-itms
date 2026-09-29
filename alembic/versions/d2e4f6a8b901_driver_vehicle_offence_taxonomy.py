"""split offences into driver / vehicle / both and add liable_party

Adds the offence types needed to cover driver fitness offences (drunk and
reckless driving) and vehicle condition and documentation offences
(unroadworthy, expired road tax, missing number plates, illegal
modification), and records who carries the penalty on each violation.

Revision ID: d2e4f6a8b901
Revises: c1b6d7e8f901
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d2e4f6a8b901"
down_revision: Union[str, None] = "c1b6d7e8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_OFFENCE_TYPES = [
    "DRUNK_DRIVING",
    "RECKLESS_DRIVING",
    "UNROADWORTHY",
    "EXPIRED_ROAD_TAX",
    "MISSING_NUMBER_PLATES",
    "ILLEGAL_MODIFICATION",
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE cannot run inside a transaction block on older servers, and
        # the value is only visible to other sessions after commit. Nothing else
        # in this revision depends on the new labels, so autocommit is safe here.
        with bind.execution_options(isolation_level="AUTOCOMMIT"):
            for label in NEW_OFFENCE_TYPES:
                op.execute(f"ALTER TYPE violationtype ADD VALUE IF NOT EXISTS '{label}'")
    else:
        # SQLite and other engines store enums as VARCHAR, so the model change
        # needs no DDL.
        pass

    op.add_column(
        "violations",
        sa.Column(
            "liable_party",
            sa.String(length=10),
            nullable=False,
            server_default="driver",
        ),
    )
    # Existing rows predate the taxonomy. Vehicle-condition and registration
    # offences fall on the registered owner; everything else defaults to the
    # driver, which matches the column default for rows that are ambiguous.
    op.execute(
        "UPDATE violations SET liable_party = 'owner' "
        "WHERE violation_type IN ('EXPIRED_FITNESS', 'NO_PSV_PERMIT', 'BLACKLISTED_VEHICLE', "
        "'OVERLOADING', 'ILLEGAL_MODIFICATION', 'UNROADWORTHY', 'EXPIRED_ROAD_TAX', "
        "'MISSING_NUMBER_PLATES')"
    )
    op.execute("UPDATE violations SET liable_party = 'both' WHERE violation_type IN ('NO_INSURANCE', 'ILLEGAL_PARKING')")


def downgrade() -> None:
    op.drop_column("violations", "liable_party")
    # Enum labels cannot be dropped in place on PostgreSQL; recreating the type
    # is required and is intentionally left to a manual operator.
