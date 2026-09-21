"""add durable offline toll event queue

Revision ID: c1b6d7e8f901
Revises: f0a1f8296810, a7c3d91e5b20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1b6d7e8f901"
down_revision: Union[str, tuple[str, str], None] = ("f0a1f8296810", "a7c3d91e5b20")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "toll_offline_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("device_event_id", sa.String(length=100), nullable=False),
        sa.Column("plate_number", sa.String(length=20), nullable=False),
        sa.Column("gate_id", sa.String(length=100), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("toll_amount", sa.Integer(), nullable=True),
        sa.Column("cached_compliance_result", sa.String(length=20), nullable=True),
        sa.Column("cached_issues", sa.Text(), nullable=True),
        sa.Column("cached_checks", sa.Text(), nullable=True),
        sa.Column("status", sa.Enum("QUEUED", "SYNCED", "REJECTED", name="offlinetolleventstatus"), nullable=False),
        sa.Column("synced_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("sync_error", sa.String(length=300), nullable=True),
        sa.Column("received_by", sa.Uuid(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["received_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["synced_transaction_id"], ["toll_transactions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_event_id"),
    )
    op.create_index("ix_toll_offline_events_device_event_id", "toll_offline_events", ["device_event_id"])
    op.create_index("ix_toll_offline_events_plate_number", "toll_offline_events", ["plate_number"])
    op.create_index("ix_toll_offline_events_status", "toll_offline_events", ["status"])


def downgrade() -> None:
    op.drop_index("ix_toll_offline_events_status", table_name="toll_offline_events")
    op.drop_index("ix_toll_offline_events_plate_number", table_name="toll_offline_events")
    op.drop_index("ix_toll_offline_events_device_event_id", table_name="toll_offline_events")
    op.drop_table("toll_offline_events")
    sa.Enum(name="offlinetolleventstatus").drop(op.get_bind(), checkfirst=True)