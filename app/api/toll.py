"""Toll Plaza & Toll Records API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.toll import TollPaymentStatus, TollPlaza, TollRecord, VehicleClass
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/toll", tags=["Toll"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER)


class PlazaCreate(BaseModel):
    plaza_code: str
    name: str
    location: str | None = None
    road: str | None = None


class PlazaOut(BaseModel):
    id: str
    plaza_code: str
    name: str
    location: str | None
    road: str | None
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TollTransitCreate(BaseModel):
    """Record a vehicle transiting a toll plaza."""
    plate_number: str
    plaza_id: str
    vehicle_class: VehicleClass = VehicleClass.CLASS_B
    transited_at: datetime
    amount_ngwee: int = 0
    source_system: str | None = None


class TollRecordOut(BaseModel):
    id: str
    vehicle_id: str | None
    plaza_id: str | None
    plate_number: str
    vehicle_class: VehicleClass
    transited_at: datetime
    amount_ngwee: int
    payment_status: TollPaymentStatus
    payment_reference: str | None
    paid_at: datetime | None
    synced_from_offline: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def _utcnow():
    return datetime.now(timezone.utc)


@router.get("/health")
def health():
    return {"module": "toll", "status": "ok"}


# ── Plazas ─────────────────────────────────────────────────────────────────

@router.post("/plazas", response_model=PlazaOut, status_code=201)
def create_plaza(
    body: PlazaCreate,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    if db.query(TollPlaza).filter(TollPlaza.plaza_code == body.plaza_code).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Plaza code already exists."})
    plaza = TollPlaza(**body.model_dump())
    db.add(plaza)
    db.commit()
    db.refresh(plaza)
    return plaza


@router.get("/plazas", response_model=list[PlazaOut])
def list_plazas(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return db.query(TollPlaza).filter(TollPlaza.is_active == True).all()  # noqa: E712


# ── Toll Records ───────────────────────────────────────────────────────────

@router.post("/transits", response_model=TollRecordOut, status_code=201)
def record_transit(
    body: TollTransitCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    """Record a vehicle passing through a toll plaza."""
    from app.models.vehicles import Vehicle

    # Resolve vehicle ID from plate
    vehicle = db.query(Vehicle).filter(Vehicle.plate_number == body.plate_number.upper()).first()

    # Use rate from system settings if amount not provided
    if body.amount_ngwee == 0:
        from app.models.admin import SystemSetting
        import json as _json
        rate_key = f"TOLL_RATE_{body.vehicle_class.value}_NGWEE"
        setting = db.query(SystemSetting).filter(SystemSetting.key == rate_key).first()
        amount = int(_json.loads(setting.value)) if setting else 5000
    else:
        amount = body.amount_ngwee

    record = TollRecord(
        vehicle_id=vehicle.id if vehicle else None,
        plaza_id=body.plaza_id,
        plate_number=body.plate_number.upper(),
        vehicle_class=body.vehicle_class,
        transited_at=body.transited_at,
        amount_ngwee=amount,
        payment_status=TollPaymentStatus.UNPAID if amount > 0 else TollPaymentStatus.WAIVED,
        source_system=body.source_system,
    )
    db.add(record)
    db.flush()

    # Flag vehicle if blacklisted / no insurance
    if vehicle:
        from app.models.vehicles import VehicleStatus
        if vehicle.status in (VehicleStatus.BLACKLISTED, VehicleStatus.STOLEN):
            log_event(db, action="TOLL.BLACKLIST_TRANSIT", actor_id=current_user.id,
                      actor_type=ActorType.SYSTEM, resource_type="TOLL_RECORD", resource_id=record.id,
                      after={"plate": body.plate_number, "status": vehicle.status.value})

    db.commit()
    db.refresh(record)
    return record


@router.get("/transits", response_model=PagedResponse[TollRecordOut])
def list_transits(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    payment_status: TollPaymentStatus | None = None,
    plate: str | None = None,
    plaza_id: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    q = db.query(TollRecord)
    if payment_status:
        q = q.filter(TollRecord.payment_status == payment_status)
    if plate:
        q = q.filter(TollRecord.plate_number == plate.upper())
    if plaza_id:
        q = q.filter(TollRecord.plaza_id == plaza_id)
    if date_from:
        q = q.filter(TollRecord.transited_at >= date_from)
    if date_to:
        q = q.filter(TollRecord.transited_at <= date_to)
    if cursor:
        q = q.filter(TollRecord.id > cursor)
    q = q.order_by(TollRecord.transited_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.post("/transits/{record_id}/settle", response_model=TollRecordOut)
def settle_toll(
    record_id: str,
    payment_reference: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mark a toll record as PAID after payment intent settles."""
    r = db.get(TollRecord, record_id)
    if not r:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Toll record not found."})
    if r.payment_status == TollPaymentStatus.PAID:
        return r  # idempotent
    r.payment_status = TollPaymentStatus.PAID
    r.payment_reference = payment_reference
    r.paid_at = _utcnow()
    log_event(db, action="TOLL.RECORD.SETTLED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="TOLL_RECORD", resource_id=record_id)
    db.commit()
    db.refresh(r)
    return r
