"""Vehicle Registration & Management API."""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.vehicles import Vehicle, VehicleCategory, VehicleStatus
from app.schemas.common import MessageResponse, PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/vehicles", tags=["Vehicles"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR)


# ── Schemas ────────────────────────────────────────────────────────────────

class VehicleCreate(BaseModel):
    plate_number: str
    chassis_number: str
    engine_number: str | None = None
    owner_nrc: str | None = None
    owner_name: str
    owner_phone: str | None = None
    owner_address: str | None = None
    make: str
    model: str
    year: int
    color: str | None = None
    category: VehicleCategory = VehicleCategory.PRIVATE
    seating_capacity: int | None = None
    gross_weight_kg: int | None = None


class VehicleUpdate(BaseModel):
    owner_name: str | None = None
    owner_phone: str | None = None
    owner_address: str | None = None
    color: str | None = None
    status: VehicleStatus | None = None
    blacklist_reason: str | None = None
    is_roadworthy: bool | None = None


class VehicleOut(BaseModel):
    id: str
    plate_number: str
    chassis_number: str
    owner_nrc: str | None
    owner_name: str
    make: str
    model: str
    year: int
    color: str | None
    category: VehicleCategory
    status: VehicleStatus
    is_roadworthy: bool
    registration_expiry: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Helpers ────────────────────────────────────────────────────────────────

def _utcnow():
    return datetime.now(timezone.utc)


def _get_or_404(db: Session, vehicle_id: str) -> Vehicle:
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vehicle not found."})
    return v


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/health")
def health():
    return {"module": "vehicles", "status": "ok"}


@router.post("", response_model=VehicleOut, status_code=201)
def register_vehicle(
    body: VehicleCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    if db.query(Vehicle).filter(Vehicle.plate_number == body.plate_number).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Plate number already registered."})
    if db.query(Vehicle).filter(Vehicle.chassis_number == body.chassis_number).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Chassis number already registered."})

    vehicle = Vehicle(**body.model_dump())
    db.add(vehicle)
    db.flush()
    log_event(db, action="VEHICLE.REGISTERED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="VEHICLE", resource_id=vehicle.id,
              after={"plate": vehicle.plate_number})
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.get("", response_model=PagedResponse[VehicleOut])
def list_vehicles(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    status: VehicleStatus | None = None,
    owner_nrc: str | None = None,
    plate: str | None = None,
):
    q = db.query(Vehicle)
    if status:
        q = q.filter(Vehicle.status == status)
    if owner_nrc:
        q = q.filter(Vehicle.owner_nrc == owner_nrc)
    if plate:
        q = q.filter(Vehicle.plate_number.ilike(f"%{plate}%"))
    if cursor:
        q = q.filter(Vehicle.id > cursor)
    q = q.order_by(Vehicle.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/{vehicle_id}", response_model=VehicleOut)
def get_vehicle(vehicle_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, vehicle_id)


@router.get("/by-plate/{plate_number}", response_model=VehicleOut)
def get_by_plate(plate_number: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    v = db.query(Vehicle).filter(Vehicle.plate_number == plate_number.upper()).first()
    if not v:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vehicle not found."})
    return v


@router.put("/{vehicle_id}", response_model=VehicleOut)
def update_vehicle(
    vehicle_id: str,
    body: VehicleUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    v = _get_or_404(db, vehicle_id)
    before = {"status": v.status.value}
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(v, field, val)
    log_event(db, action="VEHICLE.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="VEHICLE", resource_id=vehicle_id,
              before=before, after={"status": v.status.value})
    db.commit()
    db.refresh(v)
    return v


@router.post("/{vehicle_id}/blacklist", response_model=VehicleOut)
def blacklist_vehicle(
    vehicle_id: str,
    reason: str = Query(..., min_length=5),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OFFICER)),
    db: Session = Depends(get_db),
):
    v = _get_or_404(db, vehicle_id)
    v.status = VehicleStatus.BLACKLISTED
    v.blacklist_reason = reason
    log_event(db, action="VEHICLE.BLACKLISTED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="VEHICLE", resource_id=vehicle_id,
              after={"reason": reason})
    db.commit()
    db.refresh(v)
    return v


@router.delete("/{vehicle_id}/blacklist", response_model=VehicleOut)
def remove_blacklist(
    vehicle_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    v = _get_or_404(db, vehicle_id)
    v.status = VehicleStatus.ACTIVE
    v.blacklist_reason = None
    log_event(db, action="VEHICLE.BLACKLIST_REMOVED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="VEHICLE", resource_id=vehicle_id)
    db.commit()
    db.refresh(v)
    return v
