"""Driver Licensing API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.drivers import Driver, LicenceClass, LicenceStatus
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/drivers", tags=["Drivers"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR)


class DriverCreate(BaseModel):
    nrc_number: str
    full_name: str
    date_of_birth: datetime | None = None
    phone: str | None = None
    address: str | None = None
    licence_number: str
    licence_class: LicenceClass
    issue_date: datetime | None = None
    expiry_date: datetime | None = None


class DriverUpdate(BaseModel):
    phone: str | None = None
    address: str | None = None
    licence_class: LicenceClass | None = None
    licence_status: LicenceStatus | None = None
    expiry_date: datetime | None = None
    demerit_points: int | None = None
    restrictions: str | None = None
    is_active: bool | None = None


class DriverOut(BaseModel):
    id: str
    nrc_number: str
    full_name: str
    licence_number: str
    licence_class: LicenceClass
    licence_status: LicenceStatus
    expiry_date: datetime | None
    demerit_points: int
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, driver_id: str) -> Driver:
    d = db.get(Driver, driver_id)
    if not d:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Driver not found."})
    return d


@router.get("/health")
def health():
    return {"module": "drivers", "status": "ok"}


@router.post("", response_model=DriverOut, status_code=201)
def create_driver(
    body: DriverCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    if db.query(Driver).filter(Driver.nrc_number == body.nrc_number).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "NRC already registered."})
    if db.query(Driver).filter(Driver.licence_number == body.licence_number).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Licence number already exists."})
    driver = Driver(**body.model_dump())
    db.add(driver)
    db.flush()
    log_event(db, action="DRIVER.CREATED", actor_id=current_user.id, actor_type=ActorType.OFFICER,
              resource_type="DRIVER", resource_id=driver.id)
    db.commit()
    db.refresh(driver)
    return driver


@router.get("", response_model=PagedResponse[DriverOut])
def list_drivers(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    status: LicenceStatus | None = None,
    nrc: str | None = None,
):
    q = db.query(Driver)
    if status:
        q = q.filter(Driver.licence_status == status)
    if nrc:
        q = q.filter(Driver.nrc_number == nrc)
    if cursor:
        q = q.filter(Driver.id > cursor)
    q = q.order_by(Driver.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/by-nrc/{nrc_number}", response_model=DriverOut)
def get_by_nrc(nrc_number: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    d = db.query(Driver).filter(Driver.nrc_number == nrc_number).first()
    if not d:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Driver not found."})
    return d


@router.get("/{driver_id}", response_model=DriverOut)
def get_driver(driver_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, driver_id)


@router.put("/{driver_id}", response_model=DriverOut)
def update_driver(
    driver_id: str,
    body: DriverUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    d = _get_or_404(db, driver_id)
    before = {"licence_status": d.licence_status.value, "demerit_points": d.demerit_points}
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(d, field, val)
    log_event(db, action="DRIVER.UPDATED", actor_id=current_user.id, actor_type=ActorType.OFFICER,
              resource_type="DRIVER", resource_id=driver_id, before=before)
    db.commit()
    db.refresh(d)
    return d


@router.post("/{driver_id}/suspend", response_model=DriverOut)
def suspend_licence(
    driver_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OFFICER)),
    db: Session = Depends(get_db),
):
    d = _get_or_404(db, driver_id)
    d.licence_status = LicenceStatus.SUSPENDED
    log_event(db, action="DRIVER.LICENCE_SUSPENDED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="DRIVER", resource_id=driver_id)
    db.commit()
    db.refresh(d)
    return d


@router.post("/{driver_id}/reinstate", response_model=DriverOut)
def reinstate_licence(
    driver_id: str,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    d = _get_or_404(db, driver_id)
    d.licence_status = LicenceStatus.ACTIVE
    log_event(db, action="DRIVER.LICENCE_REINSTATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="DRIVER", resource_id=driver_id)
    db.commit()
    db.refresh(d)
    return d
