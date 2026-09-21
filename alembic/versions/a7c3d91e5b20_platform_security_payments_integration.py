"""platform: security, payments ledger, settings, integration, indexes

Revision ID: a7c3d91e5b20
Revises: f0a1f8296810
Create Date: 2026-09-19 10:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "a7c3d91e5b20"
down_revision: Union[str, None] = "f0a1f8296810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("(CURRENT_TIMESTAMP)")


def _ts(name, **kw):
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def _existing_notification_channel_enum():
    if op.get_bind().dialect.name == "postgresql":
        return postgresql.ENUM(
            "SMS", "EMAIL", "IN_APP", name="notificationchannel", create_type=False
        )
    return sa.Enum("SMS", "EMAIL", "IN_APP", name="notificationchannel")


def upgrade() -> None:
    # --- users: MFA, lockout, phone ---------------------------------------
    op.add_column("users", sa.Column("phone_number", sa.String(30), nullable=True))
    op.add_column("users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("mfa_secret_enc", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("mfa_recovery_hashes", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", _ts("locked_until", nullable=True))
    op.add_column("users", _ts("last_login_at", nullable=True))
    op.add_column("users", _ts("password_changed_at", nullable=True))

    # --- payments: receipts, idempotency, refunds, reconciliation ------------
    op.add_column("payments", sa.Column("description", sa.String(300), nullable=True))
    op.add_column("payments", sa.Column("idempotency_key", sa.String(100), nullable=True))
    op.add_column("payments", sa.Column("receipt_number", sa.String(50), nullable=True))
    op.add_column("payments", sa.Column("refunded_amount", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("payments", sa.Column("failure_reason", sa.String(300), nullable=True))
    op.add_column("payments", sa.Column("gateway_payload_enc", sa.Text(), nullable=True))
    op.add_column("payments", _ts("reconciled_at", nullable=True))
    op.add_column("payments", sa.Column("reconciliation_run_id", sa.Uuid(), nullable=True))
    op.create_index("ix_payments_idempotency_key", "payments", ["idempotency_key"], unique=True)
    op.create_index("ix_payments_receipt_number", "payments", ["receipt_number"], unique=True)
    op.create_index("ix_payments_reconciliation_run_id", "payments", ["reconciliation_run_id"])

    # --- notifications: delivery tracking -----------------------------------
    op.add_column("notifications", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("notifications", sa.Column("last_error", sa.String(500), nullable=True))
    op.add_column("notifications", _ts("sent_at", nullable=True))
    op.add_column("notifications", sa.Column("dedupe_key", sa.String(200), nullable=True))
    op.create_index("ix_notifications_dedupe_key", "notifications", ["dedupe_key"])

    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", _existing_notification_channel_enum(),
                  nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("user_id", "channel", name="uq_notif_pref_user_channel"),
    )
    op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])

    # --- security: devices, sessions, login attempts -----------------------------
    op.create_table(
        "devices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("label", sa.String(200)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("last_ip", sa.String(64)),
        sa.Column("login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        _ts("first_seen_at", server_default=NOW),
        _ts("last_seen_at", server_default=NOW),
        sa.UniqueConstraint("user_id", "fingerprint", name="uq_device_user_fp"),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.Uuid(), sa.ForeignKey("devices.id")),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(300)),
        _ts("created_at", server_default=NOW),
        _ts("last_seen_at", server_default=NOW),
        _ts("expires_at", nullable=False),
        _ts("revoked_at"),
        sa.Column("revoked_reason", sa.String(100)),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])

    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(100)),
        _ts("created_at", server_default=NOW),
    )
    op.create_index("ix_login_attempts_email", "login_attempts", ["email"])
    op.create_index("ix_login_attempts_created_at", "login_attempts", ["created_at"])

    # --- RBAC + settings ------------------------------------------------------------
    op.create_table(
        "role_permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("permission", sa.String(100), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("role", "permission", name="uq_role_permission"),
    )
    op.create_index("ix_role_permissions_role", "role_permissions", ["role"])

    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id")),
        _ts("updated_at", server_default=NOW),
    )

    # --- inter-agency integration ---------------------------------------------------------
    op.create_table(
        "agency_clients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, unique=True),
        sa.Column("agency_type", sa.String(50), nullable=False),
        sa.Column("api_key_prefix", sa.String(16), nullable=False),
        sa.Column("api_key_hash", sa.String(64), nullable=False),
        sa.Column("scopes", sa.String(500), nullable=False),
        sa.Column("contact_email", sa.String(200)),
        sa.Column("data_sharing_agreement", sa.Text()),
        _ts("contract_expires_at"),
        sa.Column("rate_limit_per_minute", sa.Integer(), nullable=False, server_default="120"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        _ts("last_used_at"),
        _ts("created_at", server_default=NOW),
    )
    op.create_index("ix_agency_clients_agency_type", "agency_clients", ["agency_type"])
    op.create_index("ix_agency_clients_api_key_prefix", "agency_clients", ["api_key_prefix"])

    op.create_table(
        "integration_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("agency_id", sa.Uuid(), sa.ForeignKey("agency_clients.id")),
        sa.Column("agency_name", sa.String(200)),
        sa.Column("direction", sa.String(10), nullable=False, server_default="inbound"),
        sa.Column("endpoint", sa.String(200), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
        sa.Column("detail", sa.String(500)),
        _ts("created_at", server_default=NOW),
    )
    op.create_index("ix_integration_logs_agency_id", "integration_logs", ["agency_id"])
    op.create_index("ix_integration_logs_created_at", "integration_logs", ["created_at"])

    # --- revenue: reconciliation + append-only ledger -----------------------------------------
    op.create_table(
        "reconciliation_runs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("gateway", sa.String(50), nullable=False),
        sa.Column("run_by", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("statement_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ledger_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mismatched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_in_ledger", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("missing_in_statement", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report", sa.Text()),
        _ts("created_at", server_default=NOW),
    )
    op.create_table(
        "payment_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("note", sa.String(300)),
        _ts("created_at", server_default=NOW),
    )
    op.create_index("ix_payment_events_payment_id", "payment_events", ["payment_id"])

    # --- scalability: indexes for the hot query paths -----------------------------------------------
    for name, table, cols in [
        ("ix_payments_status_paid_at", "payments", ["status", "paid_at"]),
        ("ix_payments_paid_by_created_at", "payments", ["paid_by", "created_at"]),
        ("ix_challans_status_vehicle", "challans", ["status", "vehicle_id"]),
        ("ix_toll_transactions_timestamp", "toll_transactions", ["timestamp"]),
        ("ix_violations_timestamp", "violations", ["timestamp"]),
        ("ix_accidents_occurred_at", "accidents", ["occurred_at"]),
        ("ix_audit_logs_timestamp", "audit_logs", ["timestamp"]),
        ("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"]),
        ("ix_audit_logs_actor", "audit_logs", ["actor_id"]),
        ("ix_notifications_status_created", "notifications", ["status", "created_at"]),
        ("ix_notifications_user_read", "notifications", ["user_id", "read"]),
        ("ix_insurance_end_date", "insurance", ["end_date"]),
        ("ix_fitness_certificates_expiry", "fitness_certificates", ["expiry_date"]),
        ("ix_drivers_licence_expiry", "drivers", ["licence_expiry_date"]),
        ("ix_psv_permits_expiry", "psv_permits", ["expiry_date"]),
        ("ix_vehicles_registration_date", "vehicles", ["registration_date"]),
    ]:
        op.create_index(name, table, cols)


def downgrade() -> None:
    for name, table in [
        ("ix_payments_status_paid_at", "payments"), ("ix_payments_paid_by_created_at", "payments"),
        ("ix_challans_status_vehicle", "challans"), ("ix_toll_transactions_timestamp", "toll_transactions"),
        ("ix_violations_timestamp", "violations"), ("ix_accidents_occurred_at", "accidents"),
        ("ix_audit_logs_timestamp", "audit_logs"), ("ix_audit_logs_entity", "audit_logs"),
        ("ix_audit_logs_actor", "audit_logs"), ("ix_notifications_status_created", "notifications"),
        ("ix_notifications_user_read", "notifications"), ("ix_insurance_end_date", "insurance"),
        ("ix_fitness_certificates_expiry", "fitness_certificates"), ("ix_drivers_licence_expiry", "drivers"),
        ("ix_psv_permits_expiry", "psv_permits"), ("ix_vehicles_registration_date", "vehicles"),
    ]:
        op.drop_index(name, table_name=table)

    op.drop_table("payment_events")
    op.drop_table("reconciliation_runs")
    op.drop_table("integration_logs")
    op.drop_table("agency_clients")
    op.drop_table("system_settings")
    op.drop_table("role_permissions")
    op.drop_table("login_attempts")
    op.drop_table("user_sessions")
    op.drop_table("devices")
    op.drop_table("notification_preferences")

    op.drop_index("ix_notifications_dedupe_key", table_name="notifications")
    for col in ("dedupe_key", "sent_at", "last_error", "attempts"):
        op.drop_column("notifications", col)

    for idx in ("ix_payments_reconciliation_run_id", "ix_payments_receipt_number", "ix_payments_idempotency_key"):
        op.drop_index(idx, table_name="payments")
    for col in ("reconciliation_run_id", "reconciled_at", "gateway_payload_enc", "failure_reason",
                "refunded_amount", "receipt_number", "idempotency_key", "description"):
        op.drop_column("payments", col)

    for col in ("password_changed_at", "last_login_at", "locked_until", "failed_login_count",
                "mfa_recovery_hashes", "mfa_secret_enc", "mfa_enabled", "phone_number"):
        op.drop_column("users", col)
