import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.enforcement import Challan, ChallanStatus
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.user import User
from app.schemas.payment import PaymentCreate, PaymentResponse
from app.services.audit import log_action
from app.services.notifications import notify

router = APIRouter(prefix="/api/payments", tags=["Payments"])


def generate_payment_reference() -> str:
    return f"PAY-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def create_payment(
    db: Session,
    payment_type: PaymentType,
    related_entity_id: uuid.UUID,
    amount: int,
    actor_id: uuid.UUID,
    gateway: str = "sandbox",
) -> Payment:
    payment = Payment(
        reference=generate_payment_reference(),
        payment_type=payment_type,
        related_entity_id=related_entity_id,
        amount=amount,
        gateway=gateway,
        status=PaymentStatus.PENDING,
        paid_by=actor_id,
    )
    db.add(payment)
    db.flush()
    return payment


def complete_payment(payment: Payment) -> None:
    # Simulate sandbox gateway processing
    payment.status = PaymentStatus.COMPLETED
    payment.paid_at = datetime.utcnow()
    payment.gateway_reference = f"SANDBOX-{secrets.token_hex(6).upper()}"


@router.post("/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def initiate_payment(
    payload: PaymentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = create_payment(
        db,
        payload.payment_type,
        payload.related_entity_id,
        payload.amount,
        current_user.id,
        payload.gateway or "sandbox",
    )
    db.flush()

    # Complete immediately in sandbox mode
    complete_payment(payment)

    # Settle linked entity
    if payload.payment_type == PaymentType.FINE:
        challan = db.query(Challan).filter(Challan.id == payload.related_entity_id).first()
        if challan:
            challan.status = ChallanStatus.PAID

    db.flush()
    log_action(
        db, "pay", "payment", str(payment.id),
        f"{payment.payment_type.value} payment of {payment.amount}", current_user.id
    )
    notify(
        db,
        current_user.id,
        "payment_receipt",
        {"amount": payment.amount, "reference": payment.reference},
    )
    db.commit()
    db.refresh(payment)
    return payment


@router.get("/", response_model=list[PaymentResponse])
def list_payments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Payment).order_by(Payment.created_at.desc()).limit(100).all()