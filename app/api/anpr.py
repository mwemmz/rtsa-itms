"""ANPR (Automatic Number Plate Recognition) API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.anpr import ANPRCapture, ANPRFlagReason
from app.models.citizen import User, UserRole
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/anpr", tags=["ANPR"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER)


class ANPRCaptureCreate(BaseModel):
    plate_number: str
    camera_id: str | None = None
    location: str | None = None
    captured_at: datetime
    image_url: str | None = None
    confidence_score: float | None = None


class ANPRCaptureOut(BaseModel):
    id: str
    plate_number: str
    vehicle_id: str | None
    camera_id: str | None
    location: str | None
    captured_at: datetime
    flagged: bool
    flag_reason: ANPRFlagReason
    flag_notes: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ANPRComplianceResult(BaseModel):
    plate_number: str
    vehicle_found: bool
    flagged: bool
    flag_reason: ANPRFlagReason
    flag_notes: str | None
    vehicle_status: str | None
    has_active_insurance: bool
    capture_id: str


def _utcnow():
    return datetime.now(timezone.utc)


@router.get("/health")
def health():
    return {"module": "anpr", "status": "ok"}


@router.post("/capture", response_model=ANPRComplianceResult, status_code=201)
def process_capture(
    body: ANPRCaptureCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    """
    Process an ANPR camera capture — runs full compliance engine and flags
    the vehicle if any rule is violated.
    """
    from app.services.compliance_engine import check_plate, ComplianceFlag

    plate = body.plate_number.upper().strip()
    compliance = check_plate(db, plate)

    # Map first compliance flag to ANPRFlagReason
    flag_reason = ANPRFlagReason.NONE
    flag_notes: str | None = None
    if compliance.flags:
        flag_map = {
            ComplianceFlag.BLACKLISTED: ANPRFlagReason.BLACKLISTED,
            ComplianceFlag.STOLEN: ANPRFlagReason.STOLEN,
            ComplianceFlag.EXPIRED_INSURANCE: ANPRFlagReason.EXPIRED_INSURANCE,
            ComplianceFlag.EXPIRED_REGISTRATION: ANPRFlagReason.EXPIRED_REGISTRATION,
            ComplianceFlag.OUTSTANDING_FINES: ANPRFlagReason.OUTSTANDING_FINES,
        }
        for cf in compliance.flags:
            if cf in flag_map:
                flag_reason = flag_map[cf]
                break
            flag_reason = ANPRFlagReason.WANTED  # fallback for PSV/toll flags
        flag_notes = "; ".join(compliance.notes)

    has_active_insurance = ComplianceFlag.EXPIRED_INSURANCE not in compliance.flags

    capture = ANPRCapture(
        plate_number=plate,
        vehicle_id=compliance.vehicle_id,
        camera_id=body.camera_id,
        location=body.location,
        captured_at=body.captured_at,
        image_url=body.image_url,
        confidence_score=body.confidence_score,
        flagged=not compliance.compliant,
        flag_reason=flag_reason,
        flag_notes=flag_notes,
    )
    db.add(capture)
    db.flush()

    if not compliance.compliant:
        log_event(db, action="ANPR.VEHICLE.FLAGGED", actor_id=current_user.id,
                  actor_type=ActorType.SYSTEM, resource_type="ANPR_CAPTURE", resource_id=capture.id,
                  after={"plate": plate, "flags": [f.value for f in compliance.flags]})

    db.commit()

    return ANPRComplianceResult(
        plate_number=plate,
        vehicle_found=compliance.vehicle_found,
        flagged=not compliance.compliant,
        flag_reason=flag_reason,
        flag_notes=flag_notes,
        vehicle_status=compliance.vehicle_status,
        has_active_insurance=has_active_insurance,
        capture_id=capture.id,
    )


@router.get("/captures", response_model=PagedResponse[ANPRCaptureOut])
def list_captures(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    flagged: bool | None = None,
    plate: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    q = db.query(ANPRCapture)
    if flagged is not None:
        q = q.filter(ANPRCapture.flagged == flagged)
    if plate:
        q = q.filter(ANPRCapture.plate_number == plate.upper())
    if date_from:
        q = q.filter(ANPRCapture.captured_at >= date_from)
    if date_to:
        q = q.filter(ANPRCapture.captured_at <= date_to)
    if cursor:
        q = q.filter(ANPRCapture.id > cursor)
    q = q.order_by(ANPRCapture.captured_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/captures/{capture_id}", response_model=ANPRCaptureOut)
def get_capture(capture_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    c = db.get(ANPRCapture, capture_id)
    if not c:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Capture not found."})
    return c
