"""Revenue & payment management.

Everything that moves money goes through this module so every payment gets the
same guarantees: server-side amounts, ownership checks, idempotency, an
append-only ledger (``payment_events``), a receipt, and audit + notification.

Gateways
--------
``sandbox``          completes instantly (development / demos)
``sandbox_decline``  always declines (to exercise failure paths)
``mobile_money``     asynchronous: payment stays ``pending`` until the gateway
                     calls ``POST /api/payments/webhook/mobile_money`` with an
                     HMAC-signed body.
"""

import hashlib
import hmac
import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import encrypt
from app.core.timeutil import utcnow
from app.models.enforcement import Challan, ChallanStatus
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.platform import PaymentEvent, ReconciliationRun
from app.models.toll import TollTransaction
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify

GATEWAYS = {"sandbox", "sandbox_decline", "mobile_money"}
ASYNC_GATEWAYS = {"mobile_money"}
STAFF = {UserRole.ADMIN, UserRole.OFFICER}


def generate_payment_reference() -> str:
    return f"PAY-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def generate_receipt_number() -> str:
    return f"RCT-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def record_event(db: Session, payment: Payment, event_type: str, actor_id=None,
                 amount: int | None = None, note: str | None = None) -> PaymentEvent:
    ev = PaymentEvent(
        payment_id=payment.id,
        event_type=event_type,
        amount=payment.amount if amount is None else amount,
        actor_id=actor_id,
        note=note,
    )
    db.add(ev)
    return ev


# --- what is being paid for ---------------------------------------------------

@dataclass
class Payable:
    amount: int
    description: str
    challan: Challan | None = None
    toll: TollTransaction | None = None


def _owns_vehicle(db: Session, user: User, vehicle_id) -> bool:
    if user.role in STAFF:
        return True
    if vehicle_id is None:
        return False
    vehicle = db.get(Vehicle, vehicle_id)
    return vehicle is not None and vehicle.user_id == user.id


def resolve_payable(db: Session, user: User, payment_type: PaymentType,
                    entity_id, requested_amount: int | None) -> Payable:
    """Work out (and authorise) what is being paid. Amount comes from the
    server for fines and tolls — the client cannot choose it."""
    if payment_type == PaymentType.FINE:
        # row lock (ignored on SQLite) so two concurrent payments can't both settle it
        challan = db.query(Challan).filter(Challan.id == entity_id).with_for_update().first()
        if challan is None or not _owns_vehicle(db, user, challan.vehicle_id):
            # same answer for "missing" and "not yours": don't leak existence
            raise HTTPException(status.HTTP_404_NOT_FOUND, "e-Challan not found")
        if challan.status == ChallanStatus.PAID:
            raise HTTPException(status.HTTP_409_CONFLICT, "This e-Challan is already paid")
        amount = challan.penalty_amount
        if requested_amount is not None and requested_amount != amount:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Amount due is {amount}")
        return Payable(amount, f"e-Challan {challan.reference}", challan=challan)

    if payment_type == PaymentType.TOLL:
        toll = db.query(TollTransaction).filter(TollTransaction.id == entity_id).first()
        if toll is None or not _owns_vehicle(db, user, toll.vehicle_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Toll transaction not found")
        if toll.is_paid:
            raise HTTPException(status.HTTP_409_CONFLICT, "This toll is already paid")
        if not toll.toll_amount:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Toll transaction has no amount due")
        if requested_amount is not None and requested_amount != toll.toll_amount:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Amount due is {toll.toll_amount}")
        return Payable(toll.toll_amount, f"Toll {toll.plate_number} @ {toll.gate_id}", toll=toll)

    # fees & permits: amount comes from the request but is bounded
    if requested_amount is None or requested_amount <= 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A positive amount is required")
    if requested_amount > runtime_settings.get(db, "payments.max_amount"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Amount exceeds the maximum allowed payment")
    return Payable(requested_amount, f"{payment_type.value.capitalize()} payment")


# --- lifecycle -----------------------------------------------------------------

def create_payment(
    db: Session,
    user: User,
    payment_type: PaymentType,
    entity_id,
    requested_amount: int | None = None,
    gateway: str = "sandbox",
    idempotency_key: str | None = None,
    description: str | None = None,
) -> tuple[Payment, bool]:
    """Create and (for synchronous gateways) settle a payment.

    Returns ``(payment, created)``; ``created`` is False when an idempotent
    replay returned an existing payment.
    """
    if gateway not in GATEWAYS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported gateway. Use one of: {sorted(GATEWAYS)}")

    if idempotency_key:
        existing = db.query(Payment).filter(Payment.idempotency_key == idempotency_key).first()
        if existing is not None:
            if existing.paid_by != user.id:
                raise HTTPException(status.HTTP_409_CONFLICT, "Idempotency key already used")
            return existing, False

    payable = resolve_payable(db, user, payment_type, entity_id, requested_amount)

    # A pending async payment already covers this entity: don't double charge.
    if entity_id is not None:
        pending = db.query(Payment).filter(
            Payment.related_entity_id == entity_id,
            Payment.payment_type == payment_type,
            Payment.status == PaymentStatus.PENDING,
        ).first()
        if pending is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Payment {pending.reference} is already awaiting confirmation")

    payment = Payment(
        reference=generate_payment_reference(),
        payment_type=payment_type,
        related_entity_id=entity_id,
        amount=payable.amount,
        currency=runtime_settings.get(db, "payments.currency"),
        gateway=gateway,
        status=PaymentStatus.PENDING,
        paid_by=user.id,
        description=description or payable.description,
        idempotency_key=idempotency_key,
    )
    db.add(payment)
    db.flush()
    record_event(db, payment, "initiated", user.id)

    if gateway in ASYNC_GATEWAYS:
        payment.gateway_reference = f"MM-{secrets.token_hex(6).upper()}"
        record_event(db, payment, "awaiting_gateway", user.id, note="Waiting for gateway confirmation")
    elif gateway == "sandbox_decline":
        fail_payment(db, payment, "Declined by sandbox gateway", user.id)
    else:
        payment.gateway_reference = f"SANDBOX-{secrets.token_hex(6).upper()}"
        payment.gateway_payload_enc = encrypt(json.dumps({"gateway": gateway, "ref": payment.gateway_reference}))
        complete_payment(db, payment, user.id)

    log_action(db, "pay", "payment", str(payment.id),
               f"{payment.payment_type.value} {payment.amount} via {gateway} -> {payment.status.value}", user.id)
    return payment, True


def complete_payment(db: Session, payment: Payment, actor_id=None) -> Payment:
    if payment.status == PaymentStatus.COMPLETED:
        return payment
    payment.status = PaymentStatus.COMPLETED
    payment.paid_at = utcnow()
    payment.receipt_number = payment.receipt_number or generate_receipt_number()
    payment.failure_reason = None
    record_event(db, payment, "completed", actor_id)

    if payment.related_entity_id is not None:
        if payment.payment_type == PaymentType.FINE:
            challan = db.get(Challan, payment.related_entity_id)
            if challan is not None:
                challan.status = ChallanStatus.PAID
        elif payment.payment_type == PaymentType.TOLL:
            toll = db.get(TollTransaction, payment.related_entity_id)
            if toll is not None:
                toll.is_paid = True

    if payment.paid_by:
        notify(db, payment.paid_by, "payment_receipt",
               {"amount": payment.amount, "reference": payment.reference,
                "receipt": payment.receipt_number})
    db.flush()
    return payment


def fail_payment(db: Session, payment: Payment, reason: str, actor_id=None) -> Payment:
    if payment.status == PaymentStatus.COMPLETED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Payment already completed")
    payment.status = PaymentStatus.FAILED
    payment.failure_reason = reason[:300]
    record_event(db, payment, "failed", actor_id, note=reason[:300])
    if payment.paid_by:
        notify(db, payment.paid_by, "payment_failed", {"amount": payment.amount, "reference": payment.reference})
    db.flush()
    return payment


def refund_payment(db: Session, payment: Payment, actor: User, amount: int | None, reason: str) -> Payment:
    if payment.status not in (PaymentStatus.COMPLETED,):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only completed payments can be refunded")
    remaining = payment.amount - payment.refunded_amount
    amount = remaining if amount is None else amount
    if amount <= 0 or amount > remaining:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Refund must be between 1 and {remaining}")
    payment.refunded_amount += amount
    full = payment.refunded_amount >= payment.amount
    record_event(db, payment, "refunded" if full else "partially_refunded", actor.id, amount, reason[:300])
    if full:
        payment.status = PaymentStatus.REFUNDED
        # a fully refunded fine is owed again
        if payment.payment_type == PaymentType.FINE and payment.related_entity_id:
            challan = db.get(Challan, payment.related_entity_id)
            if challan is not None and challan.status == ChallanStatus.PAID:
                challan.status = ChallanStatus.UNPAID
        if payment.payment_type == PaymentType.TOLL and payment.related_entity_id:
            toll = db.get(TollTransaction, payment.related_entity_id)
            if toll is not None:
                toll.is_paid = False
    log_action(db, "refund", "payment", str(payment.id), f"{amount} refunded: {reason[:100]}", actor.id)
    if payment.paid_by:
        notify(db, payment.paid_by, "refund_issued", {"amount": amount, "reference": payment.reference})
    db.flush()
    return payment


# --- gateway webhook -------------------------------------------------------------

def webhook_secret(gateway: str) -> bytes:
    return hashlib.sha256(f"webhook:{gateway}:{settings.SECRET_KEY}".encode()).digest()


def sign_webhook(gateway: str, body: bytes) -> str:
    return hmac.new(webhook_secret(gateway), body, hashlib.sha256).hexdigest()


def verify_webhook(gateway: str, body: bytes, signature: str | None) -> bool:
    return bool(signature) and hmac.compare_digest(sign_webhook(gateway, body), signature)


def apply_webhook(db: Session, gateway: str, gateway_reference: str, outcome: str, reason: str | None) -> Payment:
    payment = db.query(Payment).filter(
        Payment.gateway == gateway, Payment.gateway_reference == gateway_reference
    ).first()
    if payment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown gateway reference")
    if outcome == "success":
        complete_payment(db, payment)
    elif outcome == "failed":
        if payment.status == PaymentStatus.PENDING:
            fail_payment(db, payment, reason or "Failed at gateway")
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "outcome must be 'success' or 'failed'")
    log_action(db, "gateway_webhook", "payment", str(payment.id), f"{gateway}:{outcome}", None)
    return payment


# --- reconciliation ------------------------------------------------------------------

def reconcile(db: Session, gateway: str, statement: list[dict], actor: User) -> ReconciliationRun:
    """Match a gateway settlement statement against our completed payments.

    ``statement`` rows: ``{"gateway_reference": str, "amount": int}``.
    Only payments not previously reconciled are considered.
    """
    stmt = {}
    for row in statement:
        stmt[str(row["gateway_reference"])] = int(row["amount"])

    ledger = {
        p.gateway_reference: p
        for p in db.query(Payment).filter(
            Payment.gateway == gateway,
            Payment.status.in_([PaymentStatus.COMPLETED, PaymentStatus.REFUNDED]),
            Payment.reconciled_at.is_(None),
            Payment.gateway_reference.isnot(None),
        )
    }
    run = ReconciliationRun(gateway=gateway, run_by=actor.id)
    db.add(run)
    db.flush()

    matched, mismatched, missing_ledger, missing_stmt, details = 0, 0, [], [], []
    for ref, amount in stmt.items():
        p = ledger.get(ref)
        if p is None:
            missing_ledger.append({"gateway_reference": ref, "amount": amount})
        elif p.amount != amount:
            mismatched += 1
            details.append({"reference": p.reference, "gateway_reference": ref,
                            "ledger_amount": p.amount, "statement_amount": amount})
        else:
            matched += 1
            p.reconciled_at = utcnow()
            p.reconciliation_run_id = run.id
            record_event(db, p, "reconciled", actor.id, note=f"run {run.id}")
    for ref, p in ledger.items():
        if ref not in stmt:
            missing_stmt.append({"reference": p.reference, "gateway_reference": ref, "amount": p.amount})

    run.statement_total = sum(stmt.values())
    run.ledger_total = sum(p.amount for p in ledger.values())
    run.matched = matched
    run.mismatched = mismatched
    run.missing_in_ledger = len(missing_ledger)
    run.missing_in_statement = len(missing_stmt)
    run.report = json.dumps({"mismatched": details, "missing_in_ledger": missing_ledger,
                             "missing_in_statement": missing_stmt})
    log_action(db, "reconcile", "reconciliation_run", str(run.id),
               f"{gateway}: {matched} matched, {mismatched} mismatched, "
               f"{len(missing_ledger)} not in ledger, {len(missing_stmt)} not in statement", actor.id)
    db.flush()
    return run


def revenue_summary(db: Session, since: datetime | None = None) -> dict:
    q = db.query(Payment).filter(Payment.status.in_([PaymentStatus.COMPLETED, PaymentStatus.REFUNDED]))
    if since is not None:
        q = q.filter(Payment.paid_at >= since)
    gross = q.with_entities(func.coalesce(func.sum(Payment.amount), 0)).scalar()
    refunded = q.with_entities(func.coalesce(func.sum(Payment.refunded_amount), 0)).scalar()
    by_type = {t.value if hasattr(t, "value") else t: (n, tot) for t, n, tot in
               q.with_entities(Payment.payment_type, func.count(), func.sum(Payment.amount)).group_by(Payment.payment_type)}
    by_gateway = {g: (n, tot) for g, n, tot in
                  q.with_entities(Payment.gateway, func.count(), func.sum(Payment.amount)).group_by(Payment.gateway)}
    return {
        "gross": int(gross or 0),
        "refunded": int(refunded or 0),
        "net": int((gross or 0) - (refunded or 0)),
        "by_type": {k: {"count": n, "total": int(t or 0)} for k, (n, t) in by_type.items()},
        "by_gateway": {k or "unknown": {"count": n, "total": int(t or 0)} for k, (n, t) in by_gateway.items()},
    }
