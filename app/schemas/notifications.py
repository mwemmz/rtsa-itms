"""Notification schemas — section 13."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.notifications import NotificationChannel, NotificationStatus


class NotificationTemplateCreate(BaseModel):
    template_key: str = Field(min_length=3, max_length=100)
    channel: NotificationChannel = NotificationChannel.AUTO
    subject: str | None = None
    body_template: str = Field(min_length=5)
    description: str | None = None


class NotificationTemplateOut(BaseModel):
    id: str
    template_key: str
    channel: NotificationChannel
    subject: str | None
    body_template: str
    description: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class SendNotificationRequest(BaseModel):
    """Shared event interface — Dev 1 calls this for violations, expiries, accidents, etc."""
    template_key: str
    recipient_citizen_id: str
    channel_preference: NotificationChannel = NotificationChannel.AUTO
    correlation_id: str | None = None
    variables: dict[str, Any] = Field(default_factory=dict)


class SendNotificationResponse(BaseModel):
    notification_id: str
    status: str = "QUEUED"
    message: str = "Notification queued for delivery."


class NotificationOut(BaseModel):
    id: str
    channel: NotificationChannel
    status: NotificationStatus
    subject: str | None
    body: str
    sent_at: datetime | None
    delivered_at: datetime | None
    failed_at: datetime | None
    failure_reason: str | None
    created_at: datetime

    model_config = {"from_attributes": True}
