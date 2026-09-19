"""Revenue & Payment Management — section 12."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_admin, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.payments import (
    PaymentIntent,
    PaymentStatus,
    PaymentTransaction,
    PaymentType,
    ReconciliationRun,
)
from app.schemas.common import MessageResponse, PagedResponse, PaginationMeta
from app.schemas.payments import (
    GatewayWebhookPayload,
    PaymentIntentCreate,
    PaymentIntentOut,
    ReconciliationRunOut,
    RefundCreate,
    RefundOut,
    RevenueSummary,
    TransactionOut,
)
from app.services.payments import (
    create_payment_intent,
    create_refund,
    get_idempotency_record,
    get_revenue_summary,
    process_gateway_webhook,
    run_reconciliation,
    store_idempotency_record,
)

router = APIRouter(prefix="/v1/payments", tags=["Payments"])
revenue_router = APIRouter(prefix="/v1/revenue", tags=["Revenue"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _tx_out(tx: PaymentTransaction) -> TransactionOut:
    return TransactionOut(
        id=tx.id,
        intent_id=tx.intent_id,
        gateway_transaction_id=tx.gateway_transaction_id,
        amount_ngwee=tx.amount_ngwee,
        currency=tx.currency,
        status=tx.status,
        payment_type=tx.payment_type,
        reference_id=tx.reference_id,
        receipt_number=tx.receipt_number,
        reconciliation_status=tx.reconciliation_status,
        settled_at=tx.settled_at,
        created_at=tx.created_at,
    )


# ---------------------------------------------------------------------------
# Payment Intents
# ---------------------------------------------------------------------------

@router.post("/intents", response_model=PaymentIntentOut, status_code=201)
def create_intent(
    body: PaymentIntentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Resolve idempotency key (header wins over body)
    idem_key = idempotency_key or body.idempotency_key
    if idem_key:
        existing = get_idempotency_record(db, idem_key)
        if existing:
            return json.loads(existing.response_body)

    intent = create_payment_intent(
        db,
        payer_id=current_user.id,
        payment_type=body.payment_type,
        amount_ngwee=body.amount_ngwee,
        currency=body.currency,
        reference_id=body.reference_id,
        description=body.description,
        idempotency_key=idem_key,
    )
    log_event(db, action="PAYMENT.INTENT.CREATED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="PAYMENT_INTENT", resource_id=intent.id,
              after={"amount_ngwee": intent.amount_ngwee, "type": intent.payment_type.value})

    out = PaymentIntentOut(
        id=intent.id,
        payment_type=intent.payment_type,
        reference_id=intent.reference_id,
        amount_ngwee=intent.amount_ngwee,
        currency=intent.currency,
        status=intent.status,
        gateway_redirect_url=intent.gateway_redirect_url,
        created_at=intent.created_at,
    )

    if idem_key:
        store_idempotency_record(
            db, idem_key, "/v1/payments/intents",
            "", out.model_dump_json(), 201
        )

    db.commit()
    return out


@router.get("/intents/{intent_id}", response_model=PaymentIntentOut)
def get_intent(
    intent_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    intent = db.get(PaymentIntent, intent_id)
    if not intent:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Payment intent not found."})
    # Citizens can only see their own intents; staff can see all
    if current_user.role == "CITIZEN" and intent.payer_id != current_user.id:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Access denied."})
    return PaymentIntentOut(
        id=intent.id,
        payment_type=intent.payment_type,
        reference_id=intent.reference_id,
        amount_ngwee=intent.amount_ngwee,
        currency=intent.currency,
        status=intent.status,
        gateway_redirect_url=intent.gateway_redirect_url,
        created_at=intent.created_at,
    )


# ---------------------------------------------------------------------------
# Gateway webhook (inbound from payment provider — no auth, signature-verified)
# ---------------------------------------------------------------------------

@router.post("/webhooks/gateway", status_code=200)
def gateway_webhook(body: GatewayWebhookPayload, db: Session = Depends(get_db)):
    payload_dict = body.model_dump()
    signature = payload_dict.pop("signature")
    try:
        tx = process_gateway_webhook(db, payload=payload_dict, signature=signature)
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": str(exc)})

    log_event(db, action=f"PAYMENT.TRANSACTION.{tx.status.value}", actor_type=ActorType.SYSTEM,
              resource_type="PAYMENT_TRANSACTION", resource_id=tx.id,
              after={"status": tx.status.value, "amount_ngwee": tx.amount_ngwee})
    db.commit()
    return {"received": True, "transaction_id": tx.id, "status": tx.status.value}


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@router.get("/transactions", response_model=PagedResponse[TransactionOut])
def list_transactions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = Query(default=None),
    status: PaymentStatus | None = Query(default=None),
    payment_type: PaymentType | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    q = db.query(PaymentTransaction)
    # Citizens only see their own transactions
    if current_user.role == UserRole.CITIZEN:
        q = q.filter(PaymentTransaction.payer_id == current_user.id)
    if status:
        q = q.filter(PaymentTransaction.status == status)
    if payment_type:
        q = q.filter(PaymentTransaction.payment_type == payment_type)
    if date_from:
        q = q.filter(PaymentTransaction.created_at >= date_from)
    if date_to:
        q = q.filter(PaymentTransaction.created_at <= date_to)
    if cursor:
        q = q.filter(PaymentTransaction.id > cursor)
    q = q.order_by(PaymentTransaction.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=[_tx_out(t) for t in items],
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/transactions/{tx_id}", response_model=TransactionOut)
def get_transaction(
    tx_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tx = db.get(PaymentTransaction, tx_id)
    if not tx:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Transaction not found."})
    if current_user.role == UserRole.CITIZEN and tx.payer_id != current_user.id:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Access denied."})
    return _tx_out(tx)


@router.get("/transactions/{tx_id}/receipt")
def get_receipt(
    tx_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    tx = db.get(PaymentTransaction, tx_id)
    if not tx:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Transaction not found."})
    if current_user.role == UserRole.CITIZEN and tx.payer_id != current_user.id:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Access denied."})

    # Stub: return a simple text receipt (PDF generation via ReportLab/weasyprint in production)
    receipt_text = (
        f"RTSA ITMS RECEIPT\n"
        f"{'='*40}\n"
        f"Receipt No:   {tx.receipt_number}\n"
        f"Transaction:  {tx.id}\n"
        f"Type:         {tx.payment_type.value}\n"
        f"Amount:       ZMW {tx.amount_ngwee / 100:.2f}\n"
        f"Status:       {tx.status.value}\n"
        f"Date:         {tx.created_at.isoformat()}\n"
        f"{'='*40}\n"
        f"Thank you for your payment.\n"
    )
    return Response(content=receipt_text, media_type="text/plain")


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

@router.post("/reconciliation/runs", response_model=ReconciliationRunOut, status_code=201)
def start_reconciliation(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = run_reconciliation(db, initiated_by=current_user.id)
    log_event(db, action="PAYMENT.RECONCILIATION.STARTED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="RECONCILIATION_RUN", resource_id=run.id)
    db.commit()
    db.refresh(run)
    return ReconciliationRunOut(
        id=run.id,
        status=run.status,
        total_records=run.total_records,
        matched=run.matched,
        mismatched=run.mismatched,
        missing=run.missing,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


@router.get("/reconciliation/runs/{run_id}", response_model=ReconciliationRunOut)
def get_reconciliation_run(
    run_id: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = db.get(ReconciliationRun, run_id)
    if not run:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Reconciliation run not found."})
    return ReconciliationRunOut(
        id=run.id,
        status=run.status,
        total_records=run.total_records,
        matched=run.matched,
        mismatched=run.mismatched,
        missing=run.missing,
        started_at=run.started_at,
        completed_at=run.completed_at,
    )


# ---------------------------------------------------------------------------
# Refunds (admin only)
# ---------------------------------------------------------------------------

@router.post("/refunds", response_model=RefundOut, status_code=201)
def create_refund_endpoint(
    body: RefundCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    idem_key = idempotency_key or body.idempotency_key
    if idem_key:
        existing = get_idempotency_record(db, idem_key)
        if existing:
            return json.loads(existing.response_body)

    try:
        refund = create_refund(
            db,
            transaction_id=body.transaction_id,
            amount_ngwee=body.amount_ngwee,
            justification=body.justification,
            initiated_by=current_user.id,
            idempotency_key=idem_key,
        )
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": str(exc)})

    log_event(db, action="PAYMENT.REFUND.CREATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="PAYMENT_REFUND", resource_id=refund.id,
              after={"transaction_id": body.transaction_id, "amount_ngwee": body.amount_ngwee})

    out = RefundOut(
        id=refund.id,
        transaction_id=refund.transaction_id,
        amount_ngwee=refund.amount_ngwee,
        currency=refund.currency,
        justification=refund.justification,
        status=refund.status,
        created_at=refund.created_at,
    )

    if idem_key:
        store_idempotency_record(db, idem_key, "/v1/payments/refunds", "", out.model_dump_json(), 201)

    db.commit()
    return out


# ---------------------------------------------------------------------------
# Revenue summary
# ---------------------------------------------------------------------------

@revenue_router.get("/summary", response_model=RevenueSummary)
def revenue_summary(
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.AUDITOR, UserRole.OFFICER)),
    db: Session = Depends(get_db),
):
    data = get_revenue_summary(db, date_from=date_from, date_to=date_to)
    return RevenueSummary(**data)
