"""Payment & revenue schemas — section 12."""

from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.payments import PaymentStatus, PaymentType, ReconciliationStatus


# ---------------------------------------------------------------------------
# Payment Intent
# ---------------------------------------------------------------------------

class PaymentIntentCreate(BaseModel):
    payment_type: PaymentType
    reference_id: str | None = None
    amount_ngwee: int = Field(gt=0, description="Amount in ngwee (integer minor units). Never floats.")
    currency: str = Field(default="ZMW", max_length=3)
    description: str | None = None
    idempotency_key: str | None = None  # also passed as header; body wins if both present


class PaymentIntentOut(BaseModel):
    id: str
    payment_type: PaymentType
    reference_id: str | None
    amount_ngwee: int
    currency: str
    status: PaymentStatus
    gateway_redirect_url: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Transaction
# ---------------------------------------------------------------------------

class TransactionOut(BaseModel):
    id: str
    intent_id: str
    gateway_transaction_id: str | None
    amount_ngwee: int
    currency: str
    status: PaymentStatus
    payment_type: PaymentType
    reference_id: str | None
    receipt_number: str
    reconciliation_status: ReconciliationStatus
    settled_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Gateway webhook (inbound from payment provider)
# ---------------------------------------------------------------------------

class GatewayWebhookPayload(BaseModel):
    gateway_transaction_id: str
    intent_id: str
    status: str  # raw gateway status string, mapped in service layer
    amount_ngwee: int
    currency: str = "ZMW"
    gateway_response: dict | None = None
    signature: str  # verified by service layer


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

class RefundCreate(BaseModel):
    transaction_id: str
    amount_ngwee: int = Field(gt=0)
    justification: str = Field(min_length=10)
    idempotency_key: str | None = None


class RefundOut(BaseModel):
    id: str
    transaction_id: str
    amount_ngwee: int
    currency: str
    justification: str
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

class ReconciliationRunOut(BaseModel):
    id: str
    status: str
    total_records: int
    matched: int
    mismatched: int
    missing: int
    started_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Revenue summary
# ---------------------------------------------------------------------------

class RevenueSummary(BaseModel):
    total_settled_ngwee: int
    total_refunded_ngwee: int
    net_ngwee: int
    breakdown_by_type: dict[str, int]  # PaymentType -> ngwee
    period_from: str | None
    period_to: str | None
