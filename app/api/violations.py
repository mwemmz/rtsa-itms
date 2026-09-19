"""Traffic Violations / e-Challan API."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.violations import Violation, ViolationStatus, ViolationType
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/violations", tags=["Violations"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER)


class ViolationCreate(BaseModel):
    vehicle_id: str | None = None
    driver_id: str | None = None
    offender_nrc: str | None = None
    plate_number: str | None = None
    violation_type: ViolationType
    description: str | None = None
    location: str | None = None
    occurred_at: datetime
    fine_amount_ngwee: int = 0


class ViolationUpdate(BaseModel):
    status: ViolationStatus | None = None
    payment_reference: str | None = None
    description: str | None = None


class ViolationOut(BaseModel):
    id: str
    challan_number: str
    plate_number: str | None
    offender_nrc: str | None
    violation_type: ViolationType
    description: str | None
    location: str | None
    occurred_at: datetime
    fine_amount_ngwee: int
    status: ViolationStatus
    payment_reference: str | None
    paid_at: datetime | None
    escalation_due_date: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, violation_id: str) -> Violation:
    v = db.get(Violation, violation_id)
    if not v:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Violation not found."})
    return v


def _utcnow():
    return datetime.now(timezone.utc)


@router.get("/health")
def health():
    return {"module": "violations", "status": "ok"}


@router.post("", response_model=ViolationOut, status_code=201)
def issue_violation(
    body: ViolationCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    """Issue a new traffic violation / e-Challan."""
    from app.core.config import settings

    # Pull grace period from system thresholds (default 7 days)
    from app.models.admin import SystemThreshold
    import json as _json
    thresh = db.query(SystemThreshold).filter(SystemThreshold.key == "FINE_ESCALATION_DAYS").first()
    escalation_days = int(_json.loads(thresh.value)) if thresh else 30

    violation = Violation(
        **body.model_dump(),
        issued_by=current_user.id,
        escalation_due_date=_utcnow() + timedelta(days=escalation_days),
    )
    db.add(violation)
    db.flush()

    log_event(db, action="VIOLATION.CITATION.ISSUED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="VIOLATION", resource_id=violation.id,
              after={"challan": violation.challan_number, "type": body.violation_type.value,
                     "amount_ngwee": body.fine_amount_ngwee})

    # Trigger notification if offender NRC maps to a registered citizen
    if body.offender_nrc:
        _notify_violation(db, violation, body.offender_nrc, current_user)

    db.commit()
    db.refresh(violation)
    return violation


def _notify_violation(db: Session, violation: Violation, nrc: str, issuer: User):
    """Fire-and-forget notification to the offender."""
    from app.models.citizen import User as CitizenUser
    from app.services.notifications import send_notification
    citizen = db.query(CitizenUser).filter(CitizenUser.nrc_number == nrc).first()
    if citizen:
        try:
            send_notification(
                db,
                template_key="CITATION_ISSUED",
                recipient=citizen,
                variables={
                    "citationNumber": violation.challan_number,
                    "violationType": violation.violation_type.value,
                    "issuedAt": violation.occurred_at.strftime("%Y-%m-%d"),
                    "amount": f"{violation.fine_amount_ngwee / 100:.2f}",
                },
            )
        except Exception:
            pass  # notifications are best-effort


@router.get("", response_model=PagedResponse[ViolationOut])
def list_violations(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    status: ViolationStatus | None = None,
    offender_nrc: str | None = None,
    plate: str | None = None,
    violation_type: ViolationType | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    q = db.query(Violation)
    if status:
        q = q.filter(Violation.status == status)
    if offender_nrc:
        q = q.filter(Violation.offender_nrc == offender_nrc)
    if plate:
        q = q.filter(Violation.plate_number == plate.upper())
    if violation_type:
        q = q.filter(Violation.violation_type == violation_type)
    if date_from:
        q = q.filter(Violation.occurred_at >= date_from)
    if date_to:
        q = q.filter(Violation.occurred_at <= date_to)
    if cursor:
        q = q.filter(Violation.id > cursor)
    q = q.order_by(Violation.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/{violation_id}", response_model=ViolationOut)
def get_violation(violation_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, violation_id)


@router.get("/by-challan/{challan_number}", response_model=ViolationOut)
def get_by_challan(challan_number: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v = db.query(Violation).filter(Violation.challan_number == challan_number).first()
    if not v:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Violation not found."})
    return v


@router.put("/{violation_id}", response_model=ViolationOut)
def update_violation(
    violation_id: str,
    body: ViolationUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    v = _get_or_404(db, violation_id)
    before = {"status": v.status.value}
    if body.status:
        if v.status == ViolationStatus.PAID and body.status != ViolationStatus.PAID:
            raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "Cannot change status of a PAID violation."})
        if body.status == ViolationStatus.PAID:
            v.paid_at = _utcnow()
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(v, field, val)
    log_event(db, action="VIOLATION.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="VIOLATION", resource_id=violation_id,
              before=before, after={"status": v.status.value})
    db.commit()
    db.refresh(v)
    return v


@router.post("/{violation_id}/settle", response_model=ViolationOut)
def settle_violation(
    violation_id: str,
    payment_reference: str = Query(..., description="Payment transaction ID from payment service"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Called by the payment service (or citizen) to mark a violation as PAID.
    The payment_reference is the transaction ID from POST /v1/payments/intents.
    """
    v = _get_or_404(db, violation_id)
    if v.status == ViolationStatus.PAID:
        return v  # idempotent
    if v.status == ViolationStatus.CANCELLED:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "Cannot pay a cancelled violation."})
    v.status = ViolationStatus.PAID
    v.payment_reference = payment_reference
    v.paid_at = _utcnow()
    log_event(db, action="VIOLATION.SETTLED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="VIOLATION", resource_id=violation_id,
              after={"payment_reference": payment_reference})
    db.commit()
    db.refresh(v)
    return v
