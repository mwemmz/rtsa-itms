"""Payment service — intent creation, gateway webhook handling, reconciliation."""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import get_logger
from app.models.payments import (
    IdempotencyRecord,
    PaymentIntent,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    PaymentType,
    ReconciliationRun,
    ReconciliationStatus,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Idempotency helpers
# ---------------------------------------------------------------------------

def _hash_payload(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def get_idempotency_record(db: Session, key: str) -> IdempotencyRecord | None:
    return db.query(IdempotencyRecord).filter(
        IdempotencyRecord.idempotency_key == key,
        IdempotencyRecord.expires_at > datetime.now(timezone.utc),
    ).first()


def store_idempotency_record(
    db: Session, key: str, endpoint: str, payload_hash: str, response_body: str, status_code: int
) -> IdempotencyRecord:
    record = IdempotencyRecord(
        idempotency_key=key,
        endpoint=endpoint,
        request_hash=payload_hash,
        response_body=response_body,
        status_code=status_code,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.idempotency_ttl_hours),
    )
    db.add(record)
    db.flush()
    return record


# ---------------------------------------------------------------------------
# Payment Intent
# ---------------------------------------------------------------------------

def create_payment_intent(
    db: Session,
    *,
    payer_id: str,
    payment_type: PaymentType,
    amount_ngwee: int,
    currency: str = "ZMW",
    reference_id: str | None = None,
    description: str | None = None,
    idempotency_key: str | None = None,
) -> PaymentIntent:
    intent = PaymentIntent(
        payer_id=payer_id,
        payment_type=payment_type,
        reference_id=reference_id,
        amount_ngwee=amount_ngwee,
        currency=currency,
        description=description,
        status=PaymentStatus.PENDING,
        idempotency_key=idempotency_key,
        # Sandbox: generate a fake redirect URL
        gateway_redirect_url=f"https://sandbox.payment.gw/pay/{uuid.uuid4().hex}",
        gateway_token=uuid.uuid4().hex,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
    )
    db.add(intent)
    db.flush()
    logger.info("Created payment intent id=%s amount_ngwee=%d", intent.id, intent.amount_ngwee)
    return intent


# ---------------------------------------------------------------------------
# Gateway webhook processing
# ---------------------------------------------------------------------------

def verify_gateway_signature(payload: dict, signature: str) -> bool:
    """Stub: in production validate HMAC from gateway using PAYMENT_GATEWAY_KEY."""
    expected = hashlib.sha256(
        (json.dumps(payload, sort_keys=True) + settings.payment_gateway_key).encode()
    ).hexdigest()
    # Always pass in sandbox / dev
    if settings.env == "development":
        return True
    return expected == signature


def process_gateway_webhook(db: Session, *, payload: dict, signature: str) -> PaymentTransaction:
    if not verify_gateway_signature(payload, signature):
        raise ValueError("Invalid gateway signature")

    intent_id = payload.get("intent_id")
    intent = db.get(PaymentIntent, intent_id)
    if not intent:
        raise ValueError(f"PaymentIntent {intent_id} not found")

    gateway_status = str(payload.get("status", "")).upper()
    tx_status = PaymentStatus.SETTLED if gateway_status in ("SUCCESS", "SETTLED", "PAID") else PaymentStatus.FAILED

    # Check if transaction already exists (idempotent)
    existing = db.query(PaymentTransaction).filter(
        PaymentTransaction.intent_id == intent_id
    ).first()
    if existing:
        return existing

    tx = PaymentTransaction(
        intent_id=intent_id,
        gateway_transaction_id=payload.get("gateway_transaction_id"),
        amount_ngwee=payload.get("amount_ngwee", intent.amount_ngwee),
        currency=payload.get("currency", "ZMW"),
        status=tx_status,
        payment_type=intent.payment_type,
        reference_id=intent.reference_id,
        payer_id=intent.payer_id,
        reconciliation_status=ReconciliationStatus.PENDING,
        gateway_response=json.dumps(payload),
        settled_at=datetime.now(timezone.utc) if tx_status == PaymentStatus.SETTLED else None,
    )
    db.add(tx)

    intent.status = tx_status
    db.flush()

    logger.info("Processed webhook intent=%s status=%s tx=%s", intent_id, tx_status.value, tx.id)
    return tx


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------

def create_refund(
    db: Session,
    *,
    transaction_id: str,
    amount_ngwee: int,
    justification: str,
    initiated_by: str,
    idempotency_key: str | None = None,
) -> PaymentRefund:
    tx = db.get(PaymentTransaction, transaction_id)
    if not tx:
        raise ValueError(f"Transaction {transaction_id} not found")
    if tx.status != PaymentStatus.SETTLED:
        raise ValueError("Can only refund SETTLED transactions")

    refund = PaymentRefund(
        transaction_id=transaction_id,
        amount_ngwee=amount_ngwee,
        justification=justification,
        initiated_by=initiated_by,
        status="PENDING",
        idempotency_key=idempotency_key,
    )
    db.add(refund)

    # Update transaction status
    tx.status = PaymentStatus.REFUNDED
    db.flush()
    logger.info("Created refund for tx=%s amount_ngwee=%d", transaction_id, amount_ngwee)
    return refund


# ---------------------------------------------------------------------------
# Revenue summary
# ---------------------------------------------------------------------------

def get_revenue_summary(
    db: Session, *, date_from: str | None = None, date_to: str | None = None
) -> dict:
    from sqlalchemy import func as sqlfunc
    from app.models.payments import PaymentTransaction, PaymentStatus, PaymentType

    q = db.query(PaymentTransaction).filter(
        PaymentTransaction.status == PaymentStatus.SETTLED
    )
    if date_from:
        q = q.filter(PaymentTransaction.settled_at >= date_from)
    if date_to:
        q = q.filter(PaymentTransaction.settled_at <= date_to)

    txns = q.all()

    total_settled = sum(t.amount_ngwee for t in txns)
    total_refunded = sum(
        r.amount_ngwee
        for t in txns
        for r in t.refunds
    )

    breakdown: dict[str, int] = {}
    for pt in PaymentType:
        breakdown[pt.value] = sum(t.amount_ngwee for t in txns if t.payment_type == pt)

    return {
        "total_settled_ngwee": total_settled,
        "total_refunded_ngwee": total_refunded,
        "net_ngwee": total_settled - total_refunded,
        "breakdown_by_type": breakdown,
        "period_from": date_from,
        "period_to": date_to,
    }


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def run_reconciliation(db: Session, *, initiated_by: str) -> ReconciliationRun:
    run = ReconciliationRun(initiated_by=initiated_by, status="RUNNING")
    db.add(run)
    db.flush()

    # Find all SETTLED transactions not yet MATCHED
    pending_txns = db.query(PaymentTransaction).filter(
        PaymentTransaction.reconciliation_status == ReconciliationStatus.PENDING,
        PaymentTransaction.status == PaymentStatus.SETTLED,
    ).all()

    matched = mismatched = missing = 0
    for tx in pending_txns:
        # Stub logic: any tx with a gateway_transaction_id is MATCHED
        if tx.gateway_transaction_id:
            tx.reconciliation_status = ReconciliationStatus.MATCHED
            matched += 1
        else:
            tx.reconciliation_status = ReconciliationStatus.MISSING
            missing += 1

    run.status = "COMPLETED"
    run.total_records = len(pending_txns)
    run.matched = matched
    run.mismatched = mismatched
    run.missing = missing
    run.completed_at = datetime.now(timezone.utc)
    db.flush()

    logger.info("Reconciliation run=%s matched=%d missing=%d", run.id, matched, missing)
    return run
