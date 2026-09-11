from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.insurance import Insurance
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.insurance import InsuranceCreate, InsuranceResponse
from app.services.audit import log_action

router = APIRouter(prefix="/api/insurance", tags=["Insurance"])


@router.post("/", response_model=InsuranceResponse, status_code=status.HTTP_201_CREATED)
def add_insurance(
    payload: InsuranceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == payload.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    existing = db.query(Insurance).filter(
        Insurance.policy_number == payload.policy_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Policy number already exists")

    # Deactivate any overlapping active policy for this vehicle
    db.query(Insurance).filter(
        Insurance.vehicle_id == payload.vehicle_id, Insurance.is_active == True
    ).update({"is_active": False})

    insurance = Insurance(**payload.model_dump())
    db.add(insurance)
    db.flush()
    log_action(
        db, "create", "insurance", str(insurance.id),
        f"Added insurance for {vehicle.registration_number}", current_user.id
    )
    db.commit()
    db.refresh(insurance)
    return insurance


@router.get("/vehicle/{vehicle_id}", response_model=list[InsuranceResponse])
def list_insurance_for_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Insurance)
        .filter(Insurance.vehicle_id == vehicle_id)
        .order_by(Insurance.start_date.desc())
        .all()
    )


@router.get("/vehicle/{vehicle_id}/active", response_model=InsuranceResponse)
def get_active_insurance(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    insurance = (
        db.query(Insurance)
        .filter(
            Insurance.vehicle_id == vehicle_id,
            Insurance.is_active == True,
            Insurance.end_date >= datetime.utcnow(),
        )
        .order_by(Insurance.end_date.desc())
        .first()
    )
    if not insurance:
        raise HTTPException(status_code=404, detail="No active insurance for this vehicle")
    return insurance
