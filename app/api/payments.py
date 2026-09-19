import json
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission, user_has_permission
from app.core.security import get_current_user
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.platform import PaymentEvent, ReconciliationRun
from app.models.user import User
from app.schemas.payment import (
    PaymentCreate,
    PaymentEventResponse,
    PaymentResponse,
    ReconcileRequest,
    ReconciliationResponse,
    RefundRequest,
    WebhookPayload,
)
from app.services import exporters
from app.services import payments as svc
from app.services.audit import log_action

# Re-exported for older importers.
generate_payment_reference = svc.generate_payment_reference

router = APIRouter(prefix="/api/payments", tags=["Payments"])


def _visible(db: Session, user: User, payment: Payment) -> bool:
    return payment.paid_by == user.id or user_has_permission(db, user, "payments:view_all")


def _get_visible(db: Session, user: User, payment_id: str) -> Payment:
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if payment is None or not _visible(db, user, payment):
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.post("/", response_model=PaymentResponse, status_code=status.HTTP_201_CREATED)
def initiate_payment(
    payload: PaymentCreate,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Pay a fine, toll, fee or permit.

    Fines and tolls are charged at the amount the server holds. Send an
    ``Idempotency-Key`` header (or body field) so a retried request never
    charges twice.
    """
    payment, created = svc.create_payment(
        db,
        current_user,
        payload.payment_type,
        payload.related_entity_id,
        payload.amount,
        payload.gateway or "sandbox",
        idempotency_key or payload.idempotency_key,
        payload.description,
    )
    db.commit()
    db.refresh(payment)
    if not created:
        response.status_code = status.HTTP_200_OK
    return payment


@router.get("/", response_model=list[PaymentResponse])
def list_payments(
    all_users: bool = Query(False, alias="all", description="Every payment (needs payments:view_all)"),
    status_filter: PaymentStatus | None = Query(None, alias="status"),
    payment_type: PaymentType | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Payment)
    if all_users:
        if not user_has_permission(db, current_user, "payments:view_all"):
            raise HTTPException(status_code=403, detail="Missing permission: payments:view_all")
    else:
        query = query.filter(Payment.paid_by == current_user.id)
    if status_filter:
        query = query.filter(Payment.status == status_filter)
    if payment_type:
        query = query.filter(Payment.payment_type == payment_type)
    if date_from:
        query = query.filter(Payment.created_at >= date_from)
    if date_to:
        query = query.filter(Payment.created_at <= date_to)
    return query.order_by(Payment.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/summary")
def revenue_summary(
    days: int = Query(30, ge=1, le=3650),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("payments:view_all")),
):
    from datetime import timedelta

    return svc.revenue_summary(db, since=datetime.utcnow() - timedelta(days=days))


@router.get("/{payment_id}", response_model=PaymentResponse)
def get_payment(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _get_visible(db, current_user, payment_id)


@router.get("/{payment_id}/events", response_model=list[PaymentEventResponse])
def payment_events(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    payment = _get_visible(db, current_user, payment_id)
    return (
        db.query(PaymentEvent)
        .filter(PaymentEvent.payment_id == payment.id)
        .order_by(PaymentEvent.created_at)
        .all()
    )


def _receipt(db: Session, payment: Payment) -> dict:
    if payment.status not in (PaymentStatus.COMPLETED, PaymentStatus.REFUNDED) or not payment.receipt_number:
        raise HTTPException(status_code=409, detail="No receipt: payment is not completed")
    payer = db.get(User, payment.paid_by) if payment.paid_by else None
    return {
        "receipt_number": payment.receipt_number,
        "reference": payment.reference,
        "payment_type": payment.payment_type.value,
        "description": payment.description,
        "amount": payment.amount,
        "refunded_amount": payment.refunded_amount,
        "currency": payment.currency,
        "gateway": payment.gateway,
        "gateway_reference": payment.gateway_reference,
        "status": payment.status.value,
        "paid_by_name": payer.full_name if payer else None,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else None,
    }


@router.get("/{payment_id}/receipt")
def payment_receipt(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _receipt(db, _get_visible(db, current_user, payment_id))


@router.get("/{payment_id}/receipt.pdf")
def payment_receipt_pdf(
    payment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    receipt = _receipt(db, _get_visible(db, current_user, payment_id))
    return Response(
        exporters.receipt_pdf(receipt),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{receipt["receipt_number"]}.pdf"'},
    )


@router.post("/{payment_id}/refund", response_model=PaymentResponse)
def refund(
    payment_id: str,
    payload: RefundRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("payments:refund")),
):
    payment = db.query(Payment).filter(Payment.id == payment_id).first()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    svc.refund_payment(db, payment, current_user, payload.amount, payload.reason)
    db.commit()
    db.refresh(payment)
    return payment


@router.post("/webhook/{gateway}")
async def gateway_webhook(
    gateway: str,
    request: Request,
    x_signature: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Asynchronous gateway callback. The body must be signed with HMAC-SHA256."""
    if gateway not in svc.ASYNC_GATEWAYS:
        raise HTTPException(status_code=404, detail="Unknown gateway")
    body = await request.body()
    if not svc.verify_webhook(gateway, body, x_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    try:
        payload = WebhookPayload(**json.loads(body))
    except Exception:
        raise HTTPException(status_code=400, detail="Malformed webhook body")
    payment = svc.apply_webhook(db, gateway, payload.gateway_reference, payload.outcome, payload.reason)
    db.commit()
    return {"reference": payment.reference, "status": payment.status.value}


# --- reconciliation ------------------------------------------------------------

def _run_out(run: ReconciliationRun) -> ReconciliationResponse:
    out = ReconciliationResponse.model_validate(run)
    out.details = json.loads(run.report) if run.report else None
    return out


@router.post("/reconciliation/run", response_model=ReconciliationResponse, status_code=status.HTTP_201_CREATED)
def run_reconciliation(
    payload: ReconcileRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("payments:reconcile")),
):
    run = svc.reconcile(db, payload.gateway, [r.model_dump() for r in payload.statement], current_user)
    db.commit()
    db.refresh(run)
    return _run_out(run)


@router.get("/reconciliation/runs", response_model=list[ReconciliationResponse])
def reconciliation_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("payments:reconcile")),
):
    runs = db.query(ReconciliationRun).order_by(ReconciliationRun.created_at.desc()).limit(50).all()
    return [_run_out(r) for r in runs]
