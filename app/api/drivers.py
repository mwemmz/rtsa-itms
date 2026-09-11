from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User, UserRole
from app.models.driver import Driver, DriverStatus
from app.schemas.driver import DriverCreate, DriverResponse, DriverUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/api/drivers", tags=["Drivers"])


@router.post("/", response_model=DriverResponse, status_code=status.HTTP_201_CREATED)
def create_driver(
    payload: DriverCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(Driver).filter(
        Driver.licence_number == payload.licence_number
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Driver with this licence number already exists",
        )
    driver = Driver(**payload.model_dump())
    db.add(driver)
    db.flush()
    log_action(db, "create", "driver", str(driver.id), f"Created driver licence {driver.licence_number}", current_user.id)
    db.commit()
    db.refresh(driver)
    return driver


@router.get("/", response_model=list[DriverResponse])
def list_drivers(
    search: str | None = Query(None),
    status_filter: DriverStatus | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Driver)
    if search:
        query = query.filter(
            Driver.licence_number.ilike(f"%{search}%")
            | Driver.first_name.ilike(f"%{search}%")
            | Driver.last_name.ilike(f"%{search}%")
            | Driver.id_number.ilike(f"%{search}%")
        )
    if status_filter:
        query = query.filter(Driver.status == status_filter)
    return query.offset(skip).limit(limit).all()


@router.get("/{driver_id}", response_model=DriverResponse)
def get_driver(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    return driver


@router.get("/by-licence/{licence_number}", response_model=DriverResponse)
def get_driver_by_licence(
    licence_number: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = db.query(Driver).filter(Driver.licence_number == licence_number).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    return driver


@router.patch("/{driver_id}", response_model=DriverResponse)
def update_driver(
    driver_id: str,
    payload: DriverUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(driver, field, value)
    db.flush()
    log_action(db, "update", "driver", str(driver.id), f"Updated driver {driver.licence_number}", current_user.id)
    db.commit()
    db.refresh(driver)
    return driver


@router.delete("/{driver_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_driver(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    driver.status = DriverStatus.SUSPENDED
    db.flush()
    log_action(db, "suspend", "driver", str(driver.id), f"Suspended driver {driver.licence_number}", current_user.id)
    db.commit()
    return None
