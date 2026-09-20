import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.payment import PaymentStatus, PaymentType


class PaymentCreate(BaseModel):
    payment_type: PaymentType
    related_entity_id: uuid.UUID | None = None
    # Ignored for fines and tolls (the server knows what is owed); required for fees/permits.
    amount: int | None = Field(default=None, gt=0)
    gateway_reference: str | None = None
    gateway: str | None = "sandbox"
    description: str | None = Field(default=None, max_length=300)
    idempotency_key: str | None = Field(default=None, max_length=100)


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
    receipt_number: str | None = None
    description: str | None = None
    refunded_amount: int = 0
    failure_reason: str | None = None
    reconciled_at: datetime | None = None

    model_config = {"from_attributes": True}


class RefundRequest(BaseModel):
    amount: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=3, max_length=300)


class PaymentEventResponse(BaseModel):
    id: uuid.UUID
    event_type: str
    amount: int
    actor_id: uuid.UUID | None
    note: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class WebhookPayload(BaseModel):
    gateway_reference: str
    outcome: str
    reason: str | None = None


class StatementRow(BaseModel):
    gateway_reference: str
    amount: int


class ReconcileRequest(BaseModel):
    gateway: str = "sandbox"
    statement: list[StatementRow]


class ReconciliationResponse(BaseModel):
    id: uuid.UUID
    gateway: str
    statement_total: int
    ledger_total: int
    matched: int
    mismatched: int
    missing_in_ledger: int
    missing_in_statement: int
    created_at: datetime
    details: dict | None = None

    model_config = {"from_attributes": True}
