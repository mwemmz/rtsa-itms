"""Payment, transaction and reconciliation models — Developer 2 (section 12)."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class PaymentType(str, enum.Enum):
    FINE = "FINE"
    TOLL = "TOLL"
    LICENCE_FEE = "LICENCE_FEE"
    PERMIT_FEE = "PERMIT_FEE"
    INSPECTION_FEE = "INSPECTION_FEE"
    OTHER = "OTHER"


class PaymentStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"


class ReconciliationStatus(str, enum.Enum):
    MATCHED = "MATCHED"
    MISMATCHED = "MISMATCHED"
    MISSING = "MISSING"
    PENDING = "PENDING"


# ---------------------------------------------------------------------------
# Idempotency keys (money-movement replay protection)
# ---------------------------------------------------------------------------


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    response_body: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


# ---------------------------------------------------------------------------
# Payment Intent (gateway redirect / token holder)
# ---------------------------------------------------------------------------


class PaymentIntent(Base):
    __tablename__ = "payment_intents"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Who's paying
    payer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType), nullable=False)
    # Reference to the source record in Dev 1's domain (e.g. e-Challan id, toll record id)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    # All monetary amounts in integer minor units (ngwee) — never floats
    amount_ngwee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZMW")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus), nullable=False, default=PaymentStatus.PENDING, index=True
    )
    # Gateway-issued redirect/token returned to the caller
    gateway_redirect_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    gateway_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Idempotency linkage
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    transaction: Mapped["PaymentTransaction | None"] = relationship(
        "PaymentTransaction", back_populates="intent", uselist=False
    )


# ---------------------------------------------------------------------------
# Payment Transaction (settled / failed record — immutable once SETTLED)
# ---------------------------------------------------------------------------


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    intent_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("payment_intents.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
        index=True,
    )
    gateway_transaction_id: Mapped[str | None] = mapped_column(
        String(255), unique=True, nullable=True
    )
    amount_ngwee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZMW")
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), nullable=False, index=True)
    payment_type: Mapped[PaymentType] = mapped_column(Enum(PaymentType), nullable=False)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payer_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False, index=True)
    reconciliation_status: Mapped[ReconciliationStatus] = mapped_column(
        Enum(ReconciliationStatus), nullable=False, default=ReconciliationStatus.PENDING
    )
    # Receipt metadata
    receipt_number: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        default=lambda: f"RCT-{uuid.uuid4().hex[:10].upper()}",
    )
    gateway_response: Mapped[str | None] = mapped_column(Text, nullable=True)  # raw JSON
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    intent: Mapped["PaymentIntent"] = relationship("PaymentIntent", back_populates="transaction")
    refunds: Mapped[list["PaymentRefund"]] = relationship(
        "PaymentRefund", back_populates="transaction"
    )


# ---------------------------------------------------------------------------
# Refund (admin-initiated; transactions are immutable, refunds are additive)
# ---------------------------------------------------------------------------


class PaymentRefund(Base):
    __tablename__ = "payment_refunds"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    transaction_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("payment_transactions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    amount_ngwee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="ZMW")
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    initiated_by: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    gateway_refund_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transaction: Mapped["PaymentTransaction"] = relationship(
        "PaymentTransaction", back_populates="refunds"
    )


# ---------------------------------------------------------------------------
# Reconciliation Run
# ---------------------------------------------------------------------------


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    initiated_by: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="RUNNING")
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mismatched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
