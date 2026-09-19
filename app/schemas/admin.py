"""Admin, settings, thresholds, audit-log schemas — section 14."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.admin import ActorType, ExportJobStatus


# ---------------------------------------------------------------------------
# System settings / thresholds
# ---------------------------------------------------------------------------

class SettingOut(BaseModel):
    key: str
    value: Any
    description: str | None
    requires_approval: bool
    etag: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class SettingUpdate(BaseModel):
    value: Any
    if_match: str = Field(description="ETag for optimistic concurrency")


class ThresholdOut(BaseModel):
    key: str
    value: Any
    description: str | None
    unit: str | None
    etag: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class ThresholdUpdate(BaseModel):
    value: Any
    if_match: str


# ---------------------------------------------------------------------------
# Staff user management
# ---------------------------------------------------------------------------

class StaffUserCreate(BaseModel):
    email: str
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8)
    role: str = "OFFICER"
    phone: str | None = None


class StaffUserUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    role: str | None = None


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

class AuditLogOut(BaseModel):
    id: str
    actor_id: str | None
    actor_type: ActorType
    action: str
    resource_type: str | None
    resource_id: str | None
    correlation_id: str | None
    ip_address: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Report export jobs
# ---------------------------------------------------------------------------

class ExportJobCreate(BaseModel):
    format: str = Field(pattern="^(PDF|XLSX|CSV)$")
    filters: dict[str, Any] = Field(default_factory=dict)


class ExportJobOut(BaseModel):
    id: str
    report_type: str
    format: str
    status: ExportJobStatus
    row_count: int | None
    download_url: str | None
    url_expires_at: datetime | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}
