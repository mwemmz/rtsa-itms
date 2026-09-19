"""Accident Reports API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.accidents import Accident, AccidentSeverity, AccidentStatus
from app.models.citizen import User, UserRole
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/accidents", tags=["Accidents"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER)


class AccidentCreate(BaseModel):
    occurred_at: datetime
    location: str | None = None
    description: str | None = None
    severity: AccidentSeverity = AccidentSeverity.MINOR
    driver_id: str | None = None
    driver_nrc: str | None = None
    vehicle_id: str | None = None
    plate_number: str | None = None
    fatalities: int = 0
    injuries: int = 0
    police_report_number: str | None = None
    hospital_reference: str | None = None


class AccidentUpdate(BaseModel):
    status: AccidentStatus | None = None
    severity: AccidentSeverity | None = None
    description: str | None = None
    fatalities: int | None = None
    injuries: int | None = None
    police_report_number: str | None = None
    hospital_reference: str | None = None


class AccidentOut(BaseModel):
    id: str
    report_number: str
    occurred_at: datetime
    location: str | None
    severity: AccidentSeverity
    status: AccidentStatus
    driver_nrc: str | None
    plate_number: str | None
    fatalities: int
    injuries: int
    police_report_number: str | None
    hospital_reference: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, accident_id: str) -> Accident:
    a = db.get(Accident, accident_id)
    if not a:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Accident record not found."})
    return a


def _utcnow():
    return datetime.now(timezone.utc)


@router.get("/health")
def health():
    return {"module": "accidents", "status": "ok"}


@router.post("", response_model=AccidentOut, status_code=201)
def report_accident(
    body: AccidentCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    accident = Accident(**body.model_dump(), reported_by=current_user.id)
    db.add(accident)
    db.flush()

    log_event(db, action="ACCIDENT.REPORT.FILED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="ACCIDENT", resource_id=accident.id,
              after={"report_number": accident.report_number, "severity": body.severity.value})

    # Notify involved citizen if NRC provided
    if body.driver_nrc:
        _notify_accident(db, accident, body.driver_nrc)

    db.commit()
    db.refresh(accident)
    return accident


def _notify_accident(db: Session, accident: Accident, nrc: str):
    from app.models.citizen import User as CitizenUser
    from app.services.notifications import send_notification
    citizen = db.query(CitizenUser).filter(CitizenUser.nrc_number == nrc).first()
    if citizen:
        try:
            send_notification(
                db,
                template_key="ACCIDENT_LOGGED",
                recipient=citizen,
                variables={
                    "reportNumber": accident.report_number,
                    "plateNumber": accident.plate_number or "N/A",
                    "reportDate": accident.occurred_at.strftime("%Y-%m-%d"),
                },
            )
        except Exception:
            pass


@router.get("", response_model=PagedResponse[AccidentOut])
def list_accidents(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    severity: AccidentSeverity | None = None,
    status: AccidentStatus | None = None,
    driver_nrc: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    q = db.query(Accident)
    if severity:
        q = q.filter(Accident.severity == severity)
    if status:
        q = q.filter(Accident.status == status)
    if driver_nrc:
        q = q.filter(Accident.driver_nrc == driver_nrc)
    if date_from:
        q = q.filter(Accident.occurred_at >= date_from)
    if date_to:
        q = q.filter(Accident.occurred_at <= date_to)
    if cursor:
        q = q.filter(Accident.id > cursor)
    q = q.order_by(Accident.occurred_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/{accident_id}", response_model=AccidentOut)
def get_accident(accident_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, accident_id)


@router.put("/{accident_id}", response_model=AccidentOut)
def update_accident(
    accident_id: str,
    body: AccidentUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    a = _get_or_404(db, accident_id)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(a, field, val)
    log_event(db, action="ACCIDENT.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="ACCIDENT", resource_id=accident_id)
    db.commit()
    db.refresh(a)
    return a
