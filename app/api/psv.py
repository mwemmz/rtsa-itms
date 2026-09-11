from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.psv import PSVOperator, PSVPermit, PSVPermitStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.psv import (
    PSVOperatorCreate,
    PSVOperatorResponse,
    PSVPermitCreate,
    PSVPermitResponse,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/psv", tags=["PSV"])


@router.post("/operators", response_model=PSVOperatorResponse, status_code=status.HTTP_201_CREATED)
def register_operator(
    payload: PSVOperatorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(PSVOperator).filter(
        PSVOperator.licence_number == payload.licence_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Operator licence already exists")

    operator = PSVOperator(**payload.model_dump())
    db.add(operator)
    db.flush()
    log_action(db, "register", "psv_operator", str(operator.id), f"Registered {operator.name}", current_user.id)
    db.commit()
    db.refresh(operator)
    return operator


@router.get("/operators", response_model=list[PSVOperatorResponse])
def list_operators(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(PSVOperator).all()


@router.get("/operators/{operator_id}", response_model=PSVOperatorResponse)
def get_operator(
    operator_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    operator = db.query(PSVOperator).filter(PSVOperator.id == operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")
    return operator


@router.post("/permits", response_model=PSVPermitResponse, status_code=status.HTTP_201_CREATED)
def issue_permit(
    payload: PSVPermitCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    operator = db.query(PSVOperator).filter(PSVOperator.id == payload.operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    vehicle = db.query(Vehicle).filter(Vehicle.id == payload.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    existing = db.query(PSVPermit).filter(
        PSVPermit.permit_number == payload.permit_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Permit number already exists")

    permit = PSVPermit(**payload.model_dump())
    db.add(permit)
    db.flush()
    log_action(
        db, "issue_permit", "psv_permit", str(permit.id),
        f"Permit {permit.permit_number} for {vehicle.registration_number}", current_user.id
    )
    db.commit()
    db.refresh(permit)
    return permit


@router.get("/permits", response_model=list[PSVPermitResponse])
def list_permits(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(PSVPermit).all()


@router.get("/permits/vehicle/{vehicle_id}", response_model=PSVPermitResponse)
def get_active_permit_for_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    permit = (
        db.query(PSVPermit)
        .filter(
            PSVPermit.vehicle_id == vehicle_id,
            PSVPermit.status == PSVPermitStatus.ACTIVE,
            PSVPermit.expiry_date >= datetime.utcnow(),
        )
        .first()
    )
    if not permit:
        raise HTTPException(status_code=404, detail="No active permit for this vehicle")
    return permit