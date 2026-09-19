"""Citizen portal schemas — sections 11 & 15 (user registration + applications)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field

from app.models.citizen import ApplicationStatus, ApplicationType, UserRole


# ---------------------------------------------------------------------------
# User / Profile
# ---------------------------------------------------------------------------

class UserRegister(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=255)
    password: str = Field(min_length=8)
    nrc_number: str | None = Field(default=None, max_length=50)
    phone: str | None = Field(default=None, max_length=30)


class UserProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    phone: str | None = Field(default=None, max_length=30)
    notify_sms: bool | None = None
    notify_email: bool | None = None
    notify_in_app: bool | None = None


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    nrc_number: str | None
    phone: str | None
    role: UserRole
    is_active: bool
    is_verified: bool
    mfa_enabled: bool
    notify_sms: bool
    notify_email: bool
    notify_in_app: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationPrefsUpdate(BaseModel):
    notify_sms: bool | None = None
    notify_email: bool | None = None
    notify_in_app: bool | None = None


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

class ApplicationCreate(BaseModel):
    application_type: ApplicationType
    details: dict[str, Any] = Field(default_factory=dict)


class ApplicationFulfillmentPatch(BaseModel):
    """Service-to-service endpoint — Dev 1 calls this to update application status."""
    status: ApplicationStatus
    reason: str | None = None


class ApplicationOut(BaseModel):
    id: str
    reference_number: str
    application_type: ApplicationType
    status: ApplicationStatus
    details: dict[str, Any]
    notes: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplicationStatusOut(BaseModel):
    id: str
    reference_number: str
    status: ApplicationStatus
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApplicationStatusHistoryOut(BaseModel):
    from_status: str | None
    to_status: str
    reason: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ApplicationDetailOut(ApplicationOut):
    status_history: list[ApplicationStatusHistoryOut] = []


class DocumentUploadResponse(BaseModel):
    id: str
    filename: str
    content_type: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}
