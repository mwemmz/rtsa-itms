"""PSV (Public Service Vehicle) Permits API."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.psv import PSVPermit, PSVPermitStatus, PSVRouteType
from app.models.vehicles import Vehicle
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/psv", tags=["PSV"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR)


class PSVPermitCreate(BaseModel):
    vehicle_id: str
    operator_name: str
    operator_nrc: str | None = None
    route_type: PSVRouteType = PSVRouteType.URBAN
    route_description: str | None = None
    passenger_capacity: int
    valid_from: datetime
    valid_to: datetime
    notes: str | None = None


class PSVPermitUpdate(BaseModel):
    status: PSVPermitStatus | None = None
    route_description: str | None = None
    valid_to: datetime | None = None
    notes: str | None = None


class PSVPermitOut(BaseModel):
    id: str
    vehicle_id: str
    permit_number: str
    operator_name: str
    operator_nrc: str | None
    route_type: PSVRouteType
    route_description: str | None
    passenger_capacity: int
    status: PSVPermitStatus
    valid_from: datetime
    valid_to: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, permit_id: str) -> PSVPermit:
    p = db.get(PSVPermit, permit_id)
    if not p:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "PSV permit not found."})
    return p


@router.get("/health")
def health():
    return {"module": "psv", "status": "ok"}


@router.post("/permits", response_model=PSVPermitOut, status_code=201)
def issue_permit(
    body: PSVPermitCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    vehicle = db.get(Vehicle, body.vehicle_id)
    if not vehicle:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vehicle not found."})

    permit = PSVPermit(**body.model_dump(), issued_by=current_user.id)
    db.add(permit)
    db.flush()
    log_event(db, action="PSV.PERMIT.ISSUED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="PSV_PERMIT", resource_id=permit.id,
              after={"permit_number": permit.permit_number, "vehicle_id": body.vehicle_id})
    db.commit()
    db.refresh(permit)
    return permit


@router.get("/permits", response_model=PagedResponse[PSVPermitOut])
def list_permits(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    status: PSVPermitStatus | None = None,
    route_type: PSVRouteType | None = None,
    operator_nrc: str | None = None,
):
    q = db.query(PSVPermit)
    if status:
        q = q.filter(PSVPermit.status == status)
    if route_type:
        q = q.filter(PSVPermit.route_type == route_type)
    if operator_nrc:
        q = q.filter(PSVPermit.operator_nrc == operator_nrc)
    if cursor:
        q = q.filter(PSVPermit.id > cursor)
    q = q.order_by(PSVPermit.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/permits/{permit_id}", response_model=PSVPermitOut)
def get_permit(permit_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, permit_id)


@router.put("/permits/{permit_id}", response_model=PSVPermitOut)
def update_permit(
    permit_id: str,
    body: PSVPermitUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    p = _get_or_404(db, permit_id)
    before = {"status": p.status.value}
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(p, field, val)
    log_event(db, action="PSV.PERMIT.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="PSV_PERMIT", resource_id=permit_id,
              before=before)
    db.commit()
    db.refresh(p)
    return p


@router.post("/permits/{permit_id}/suspend", response_model=PSVPermitOut)
def suspend_permit(
    permit_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OFFICER)),
    db: Session = Depends(get_db),
):
    p = _get_or_404(db, permit_id)
    p.status = PSVPermitStatus.SUSPENDED
    log_event(db, action="PSV.PERMIT.SUSPENDED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="PSV_PERMIT", resource_id=permit_id)
    db.commit()
    db.refresh(p)
    return p
