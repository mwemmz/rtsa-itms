import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.payment import PaymentStatus, PaymentType


class PaymentCreate(BaseModel):
    payment_type: PaymentType
    related_entity_id: uuid.UUID
    amount: int
    gateway_reference: str | None = None
    gateway: str | None = "sandbox"


class PaymentResponse(BaseModel):
    id: uuid.UUID
    reference: str
    payment_type: PaymentType
    related_entity_id: uuid.UUID | None
    amount: int
    currency: str
    status: PaymentStatus
    gateway_reference: str | None
    gateway: str | None
    paid_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}