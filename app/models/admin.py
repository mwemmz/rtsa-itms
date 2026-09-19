"""Admin settings, thresholds and audit log models — Developer 2 (sections 14, 0.8)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


# ---------------------------------------------------------------------------
# System Settings (fee schedules, grace periods, toll rates, etc.)
# ---------------------------------------------------------------------------


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-serialized
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    etag: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: str(uuid.uuid4())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Business Rule Thresholds
# ---------------------------------------------------------------------------


class SystemThreshold(Base):
    __tablename__ = "system_thresholds"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)  # JSON-serialized numeric or string
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g. "days", "ms", "count"
    updated_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    etag: Mapped[str] = mapped_column(
        String(64), nullable=False, default=lambda: str(uuid.uuid4())
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Audit Log (immutable — no updates, no deletes)
# ---------------------------------------------------------------------------


class ActorType(str, enum.Enum):
    CITIZEN = "CITIZEN"
    OFFICER = "OFFICER"
    ADMIN = "ADMIN"
    SYSTEM = "SYSTEM"
    AUDITOR = "AUDITOR"


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_type: Mapped[ActorType] = mapped_column(
        Enum(ActorType), nullable=False, default=ActorType.SYSTEM
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # e.g. PAYMENT.SETTLED
    resource_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    resource_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    before_state: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot
    after_state: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON snapshot
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


# ---------------------------------------------------------------------------
# Report Export Jobs
# ---------------------------------------------------------------------------


class ExportJobStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ReportExportJob(Base):
    __tablename__ = "report_export_jobs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    report_type: Mapped[str] = mapped_column(String(100), nullable=False)
    format: Mapped[str] = mapped_column(String(10), nullable=False)  # PDF | XLSX | CSV
    filters: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    requested_by: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    status: Mapped[ExportJobStatus] = mapped_column(
        Enum(ExportJobStatus), nullable=False, default=ExportJobStatus.QUEUED, index=True
    )
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    download_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    url_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Inter-Agency Integration Log / Contract Registry
# ---------------------------------------------------------------------------


class IntegrationLog(Base):
    __tablename__ = "integration_logs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    agency: Mapped[str] = mapped_column(String(50), nullable=False, index=True)  # POLICE | INSURANCE | HOSPITAL | NATIONAL_ID | TOLL
    direction: Mapped[str] = mapped_column(String(10), nullable=False)  # INBOUND | OUTBOUND
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    request_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_payload: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


class DataSharingContract(Base):
    __tablename__ = "data_sharing_contracts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    agency: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(20), nullable=False)
    allowed_fields: Mapped[str] = mapped_column(Text, nullable=False)  # JSON array
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False, default=365)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
