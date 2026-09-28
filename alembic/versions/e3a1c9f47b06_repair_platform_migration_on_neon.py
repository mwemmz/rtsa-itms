"""repair: converge the platform migration on databases where it partially applied

Revision ID: e3a1c9f47b06
Revises: c1b6d7e8f901
Create Date: 2026-09-28 09:00:00.000000

Context
-------
``a7c3d91e5b20`` originally created ``notification_preferences.channel`` with a
plain ``sa.Enum(..., create_type=False)`` reusing the ``notificationchannel``
type. On some managed Postgres setups (observed on Neon) that statement raised
because the bare ``Enum`` still attempted to touch the existing type. Since
Postgres DDL is transactional, that failure rolled back the *entire*
``a7c3d91e5b20`` migration on any database where it hit this — even though the
revision file itself was later corrected in place (d56a510), a database that
had already failed and rolled back has no record of that revision having run,
so a plain ``alembic upgrade head`` re-attempts it and should self-heal *if*
run again. This migration exists for the case where it didn't: a database
where ``alembic_version`` was hand-stamped past ``a7c3d91e5b20``, or where the
migration partially committed some other way, leaving individual tables,
columns or indexes missing while the revision is considered applied.

Every step below is guarded with an existence check via ``sa.inspect``, so
this migration converges any of those states to the correct schema and is a
complete no-op on a database where ``a7c3d91e5b20`` already applied cleanly.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "e3a1c9f47b06"
down_revision: Union[str, None] = "c1b6d7e8f901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NOW = sa.text("(CURRENT_TIMESTAMP)")


def _ts(name, **kw):
    return sa.Column(name, sa.DateTime(timezone=True), **kw)


def _notification_channel_enum(bind):
    if bind.dialect.name == "postgresql":
        return postgresql.ENUM("SMS", "EMAIL", "IN_APP", name="notificationchannel", create_type=False)
    return sa.Enum("SMS", "EMAIL", "IN_APP", name="notificationchannel")


def _tables(insp):
    return set(insp.get_table_names())


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _indexes(insp, table):
    return {i["name"] for i in insp.get_indexes(table)}


def _add_column_if_missing(insp, table, column: sa.Column):
    if table in _tables(insp) and column.name not in _columns(insp, table):
        op.add_column(table, column)


def _create_index_if_missing(insp, table, name, cols, **kw):
    if table in _tables(insp) and name not in _indexes(insp, table):
        op.create_index(name, table, cols, **kw)


def _create_table_if_missing(insp, name, *args, **kw):
    if name not in _tables(insp):
        op.create_table(name, *args, **kw)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # --- users: MFA, lockout, phone ---------------------------------------
    _add_column_if_missing(insp, "users", sa.Column("phone_number", sa.String(30), nullable=True))
    _add_column_if_missing(insp, "users", sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    _add_column_if_missing(insp, "users", sa.Column("mfa_secret_enc", sa.Text(), nullable=True))
    _add_column_if_missing(insp, "users", sa.Column("mfa_recovery_hashes", sa.Text(), nullable=True))
    _add_column_if_missing(insp, "users", sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(insp, "users", _ts("locked_until", nullable=True))
    _add_column_if_missing(insp, "users", _ts("last_login_at", nullable=True))
    _add_column_if_missing(insp, "users", _ts("password_changed_at", nullable=True))

    # --- payments: receipts, idempotency, refunds, reconciliation ------------
    _add_column_if_missing(insp, "payments", sa.Column("description", sa.String(300), nullable=True))
    _add_column_if_missing(insp, "payments", sa.Column("idempotency_key", sa.String(100), nullable=True))
    _add_column_if_missing(insp, "payments", sa.Column("receipt_number", sa.String(50), nullable=True))
    _add_column_if_missing(insp, "payments", sa.Column("refunded_amount", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(insp, "payments", sa.Column("failure_reason", sa.String(300), nullable=True))
    _add_column_if_missing(insp, "payments", sa.Column("gateway_payload_enc", sa.Text(), nullable=True))
    _add_column_if_missing(insp, "payments", _ts("reconciled_at", nullable=True))
    _add_column_if_missing(insp, "payments", sa.Column("reconciliation_run_id", sa.Uuid(), nullable=True))
    insp = sa.inspect(bind)  # refresh: columns above may be needed before indexing them
    _create_index_if_missing(insp, "payments", "ix_payments_idempotency_key", ["idempotency_key"], unique=True)
    _create_index_if_missing(insp, "payments", "ix_payments_receipt_number", ["receipt_number"], unique=True)
    _create_index_if_missing(insp, "payments", "ix_payments_reconciliation_run_id", ["reconciliation_run_id"])

    # --- notifications: delivery tracking -----------------------------------
    _add_column_if_missing(insp, "notifications", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing(insp, "notifications", sa.Column("last_error", sa.String(500), nullable=True))
    _add_column_if_missing(insp, "notifications", _ts("sent_at", nullable=True))
    _add_column_if_missing(insp, "notifications", sa.Column("dedupe_key", sa.String(200), nullable=True))
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "notifications", "ix_notifications_dedupe_key", ["dedupe_key"])

    _create_table_if_missing(
        insp, "notification_preferences",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("channel", _notification_channel_enum(bind), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("user_id", "channel", name="uq_notif_pref_user_channel"),
    )
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "notification_preferences", "ix_notification_preferences_user_id", ["user_id"])

    # --- security: devices, sessions, login attempts -----------------------------
    _create_table_if_missing(
        insp, "devices",
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
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "devices", "ix_devices_user_id", ["user_id"])

    _create_table_if_missing(
        insp, "user_sessions",
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
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "user_sessions", "ix_user_sessions_user_id", ["user_id"])

    _create_table_if_missing(
        insp, "login_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(200), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(100)),
        _ts("created_at", server_default=NOW),
    )
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "login_attempts", "ix_login_attempts_email", ["email"])
    _create_index_if_missing(insp, "login_attempts", "ix_login_attempts_created_at", ["created_at"])

    # --- RBAC + settings ------------------------------------------------------------
    _create_table_if_missing(
        insp, "role_permissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("permission", sa.String(100), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("role", "permission", name="uq_role_permission"),
    )
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "role_permissions", "ix_role_permissions_role", ["role"])

    _create_table_if_missing(
        insp, "system_settings",
        sa.Column("key", sa.String(100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), sa.ForeignKey("users.id")),
        _ts("updated_at", server_default=NOW),
    )
    insp = sa.inspect(bind)

    # --- inter-agency integration ---------------------------------------------------------
    _create_table_if_missing(
        insp, "agency_clients",
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
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "agency_clients", "ix_agency_clients_agency_type", ["agency_type"])
    _create_index_if_missing(insp, "agency_clients", "ix_agency_clients_api_key_prefix", ["api_key_prefix"])

    _create_table_if_missing(
        insp, "integration_logs",
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
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "integration_logs", "ix_integration_logs_agency_id", ["agency_id"])
    _create_index_if_missing(insp, "integration_logs", "ix_integration_logs_created_at", ["created_at"])

    # --- revenue: reconciliation + append-only ledger -----------------------------------------
    _create_table_if_missing(
        insp, "reconciliation_runs",
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
    insp = sa.inspect(bind)
    _create_table_if_missing(
        insp, "payment_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("payment_id", sa.Uuid(), sa.ForeignKey("payments.id"), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id")),
        sa.Column("note", sa.String(300)),
        _ts("created_at", server_default=NOW),
    )
    insp = sa.inspect(bind)
    _create_index_if_missing(insp, "payment_events", "ix_payment_events_payment_id", ["payment_id"])

    # --- scalability: indexes for the hot query paths -----------------------------------------------
    insp = sa.inspect(bind)
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
        _create_index_if_missing(insp, table, name, cols)


def downgrade() -> None:
    # This is a repair migration for a partially-applied predecessor; there is
    # no single well-defined prior state to return to (that depends on exactly
    # what was missing when it ran). Downgrading a7c3d91e5b20 itself still
    # removes everything both migrations are responsible for.
    pass
