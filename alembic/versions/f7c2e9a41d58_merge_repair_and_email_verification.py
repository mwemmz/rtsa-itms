"""merge heads: platform repair + email verification

Revision ID: f7c2e9a41d58
Revises: e3a1c9f47b06, d4e5f6a7b8c9
Create Date: 2026-09-28 12:00:00.000000

Two feature branches each added a migration on top of c1b6d7e8f901 (the Neon
repair and email verification). They are independent - the repair only adds
what a7c3d91e5b20 should have created, email verification only adds
users.email_verified_at - so they may run in either order. This revision joins
them back into a single head, so plain ``alembic upgrade head`` works again and
new migrations have one parent.

It also covers every database a teammate might have: one at c1b6d7e8f901, one
that already ran either branch's migration, or both.
"""
from typing import Sequence, Union

revision: str = "f7c2e9a41d58"
down_revision: Union[str, tuple[str, ...], None] = ("e3a1c9f47b06", "d4e5f6a7b8c9")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
