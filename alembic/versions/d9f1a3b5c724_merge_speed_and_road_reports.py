"""merge heads: violation speed + citizen road reports

Revision ID: d9f1a3b5c724
Revises: c4a8f2e1b705, c8e2f4a6b913
Create Date: 2026-10-05 12:00:00.000000

Two feature branches each added a migration on top of b3f5a7c9d012:
``c4a8f2e1b705`` (speed observed on a violation) and ``c8e2f4a6b913`` (citizen
road reports, NRC on accounts, false-report fines). They touch different columns
- the speed migration only adds violations.speed_kmh - so they may run in either
order. This revision joins them back into a single head, so plain
``alembic upgrade head`` works and new migrations have one parent.

It covers a database at b3f5a7c9d012, or one that already ran either branch's
migration, or both.
"""
from typing import Sequence, Union

revision: str = "d9f1a3b5c724"
down_revision: Union[str, tuple[str, ...], None] = ("c4a8f2e1b705", "c8e2f4a6b913")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
