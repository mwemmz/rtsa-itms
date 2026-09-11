import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.notification import NotificationChannel, NotificationStatus


class NotificationResponse(BaseModel):
    id: uuid.UUID
    channel: NotificationChannel
    trigger_event: str
    title: str
    body: str
    status: NotificationStatus
    read: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class NotificationRuleCreate(BaseModel):
    trigger_event: str
    channels: str
    title_template: str
    body_template: str


class NotificationRuleResponse(BaseModel):
    id: uuid.UUID
    trigger_event: str
    channels: str
    title_template: str
    body_template: str
    is_active: bool

    model_config = {"from_attributes": True}