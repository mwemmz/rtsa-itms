"""Vehicle Inspections API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.inspections import Inspection, InspectionStatus, InspectionType
from app.models.vehicles import Vehicle
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/inspections", tags=["Inspections"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR)


class InspectionCreate(BaseModel):
    vehicle_id: str
    inspection_type: InspectionType = InspectionType.ROUTINE
    scheduled_date: datetime | None = None
    inspection_centre: str | None = None
    inspector_id: str | None = None


class InspectionResult(BaseModel):
    passed: bool
    defects_found: list[str] | None = None
    notes: str | None = None
    next_due_date: datetime | None = None
    certificate_number: str | None = None


class InspectionOut(BaseModel):
    id: str
    vehicle_id: str
    inspection_type: InspectionType
    status: InspectionStatus
    inspector_id: str | None
    inspection_centre: str | None
    scheduled_date: datetime | None
    completed_date: datetime | None
    next_due_date: datetime | None
    passed: bool | None
    defects_found: str | None
    certificate_number: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, inspection_id: str) -> Inspection:
    i = db.get(Inspection, inspection_id)
    if not i:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Inspection not found."})
    return i


@router.get("/health")
def health():
    return {"module": "inspections", "status": "ok"}


@router.post("", response_model=InspectionOut, status_code=201)
def schedule_inspection(
    body: InspectionCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    if not db.get(Vehicle, body.vehicle_id):
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vehicle not found."})
    inspection = Inspection(**body.model_dump())
    db.add(inspection)
    db.flush()
    log_event(db, action="INSPECTION.SCHEDULED", actor_id=current_user.id,
              actor_type=ActorType.INSPECTOR, resource_type="INSPECTION", resource_id=inspection.id)
    db.commit()
    db.refresh(inspection)
    return inspection


@router.get("", response_model=PagedResponse[InspectionOut])
def list_inspections(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    vehicle_id: str | None = None,
    status: InspectionStatus | None = None,
):
    q = db.query(Inspection)
    if vehicle_id:
        q = q.filter(Inspection.vehicle_id == vehicle_id)
    if status:
        q = q.filter(Inspection.status == status)
    if cursor:
        q = q.filter(Inspection.id > cursor)
    q = q.order_by(Inspection.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/{inspection_id}", response_model=InspectionOut)
def get_inspection(inspection_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, inspection_id)


@router.post("/{inspection_id}/start", response_model=InspectionOut)
def start_inspection(
    inspection_id: str,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    i = _get_or_404(db, inspection_id)
    if i.status != InspectionStatus.SCHEDULED:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "Only SCHEDULED inspections can be started."})
    i.status = InspectionStatus.IN_PROGRESS
    i.inspector_id = i.inspector_id or current_user.id
    db.commit()
    db.refresh(i)
    return i


@router.post("/{inspection_id}/complete", response_model=InspectionOut)
def complete_inspection(
    inspection_id: str,
    body: InspectionResult,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    import json
    from datetime import timezone
    i = _get_or_404(db, inspection_id)
    if i.status not in (InspectionStatus.IN_PROGRESS, InspectionStatus.SCHEDULED):
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "Inspection cannot be completed in its current status."})

    i.passed = body.passed
    i.status = InspectionStatus.PASSED if body.passed else InspectionStatus.FAILED
    i.completed_date = datetime.now(timezone.utc)
    i.defects_found = json.dumps(body.defects_found) if body.defects_found else None
    i.notes = body.notes
    i.next_due_date = body.next_due_date
    i.certificate_number = body.certificate_number

    # Update vehicle roadworthy flag
    if i.vehicle_id:
        v = db.get(Vehicle, i.vehicle_id)
        if v:
            v.is_roadworthy = body.passed

    log_event(db, action="INSPECTION.COMPLETED", actor_id=current_user.id,
              actor_type=ActorType.INSPECTOR, resource_type="INSPECTION", resource_id=inspection_id,
              after={"passed": body.passed})
    db.commit()
    db.refresh(i)
    return i
