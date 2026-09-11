import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.enforcement import (
    ChallanPaymentResult,
    ChallanResponse,
    ViolationCreate,
    ViolationResponse,
)
from app.services.audit import log_action
from app.services.notifications import notify

router = APIRouter(prefix="/api/enforcement", tags=["Enforcement"])

VIOLATION_PENALTIES = {
    ViolationType.SPEEDING: 500000,
    ViolationType.RUNNING_RED_LIGHT: 300000,
    ViolationType.NO_INSURANCE: 1000000,
    ViolationType.EXPIRED_FITNESS: 800000,
    ViolationType.NO_PSV_PERMIT: 1500000,
    ViolationType.DRIVING_WITHOUT_LICENCE: 600000,
    ViolationType.ILLEGAL_PARKING: 150000,
    ViolationType.OVERLOADING: 400000,
    ViolationType.REAR_SEAT_BELT: 100000,
    ViolationType.USING_PHONE: 200000,
    ViolationType.BLACKLISTED_VEHICLE: 2000000,
    ViolationType.OTHER: 100000,
}


def generate_challan_reference() -> str:
    return f"CH-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def create_challan_for_violation(
    db: Session,
    violation: Violation,
    actor_id: uuid.UUID | None,
) -> Challan:
    challan = Challan(
        reference=generate_challan_reference(),
        violation_id=violation.id,
        vehicle_id=violation.vehicle_id,
        driver_id=violation.driver_id,
        penalty_amount=VIOLATION_PENALTIES.get(violation.violation_type, 100000),
        due_date=datetime.utcnow() + timedelta(days=14),
    )
    db.add(challan)
    db.flush()
    log_action(
        db, "generate_challan", "challan", str(challan.id),
        f"Generated {challan.reference} for {violation.violation_type.value}", actor_id
    )
    return challan


@router.post("/violations", response_model=ViolationResponse, status_code=status.HTTP_201_CREATED)
def record_violation(
    payload: ViolationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    violation = Violation(
        vehicle_id=payload.vehicle_id,
        driver_id=payload.driver_id,
        violation_type=payload.violation_type,
        location=payload.location,
        timestamp=payload.timestamp or datetime.utcnow(),
        description=payload.description,
        recorded_by=current_user.id,
    )
    db.add(violation)
    db.flush()

    challan = create_challan_for_violation(db, violation, current_user.id)
    db.flush()

    # Notify the vehicle owner if they have a user account
    vehicle = db.query(Vehicle).filter(Vehicle.id == violation.vehicle_id).first()
    owner = None
    if vehicle:
        owner = (
            db.query(User)
            .filter(User.email == vehicle.owner_id_number)
            .first()
            or db.query(User).filter(User.full_name == vehicle.owner_name).first()
        )
        if owner:
            notify(
                db,
                owner.id,
                "challan_created",
                {
                    "reference": challan.reference,
                    "amount": challan.penalty_amount,
                    "due_date": challan.due_date.strftime("%Y-%m-%d"),
                },
            )

    db.commit()
    violation_record = db.query(Violation).filter(Violation.id == violation.id).first()
    return violation_record


@router.get("/violations", response_model=list[ViolationResponse])
def list_violations(
    vehicle_id: str | None = Query(None),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Violation)
    if vehicle_id:
        query = query.filter(Violation.vehicle_id == vehicle_id)
    return query.order_by(Violation.timestamp.desc()).offset(skip).limit(limit).all()


@router.get("/challans", response_model=list[ChallanResponse])
def list_challans(
    vehicle_id: str | None = Query(None),
    status_filter: ChallanStatus | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Challan)
    if vehicle_id:
        query = query.filter(Challan.vehicle_id == vehicle_id)
    if status_filter:
        query = query.filter(Challan.status == status_filter)
    return query.order_by(Challan.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/challans/{challan_id}", response_model=ChallanResponse)
def get_challan(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")
    return challan


@router.post("/challans/{challan_id}/pay", response_model=ChallanPaymentResult)
def pay_challan(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")

    if challan.status == ChallanStatus.PAID:
        raise HTTPException(status_code=400, detail="Challan already paid")

    challan.status = ChallanStatus.PAID
    db.flush()
    log_action(db, "pay", "challan", str(challan.id), f"Paid {challan.reference}", current_user.id)
    db.commit()
    db.refresh(challan)
    return ChallanPaymentResult(
        challan_id=challan.id,
        reference=challan.reference,
        status=challan.status,
        message="Payment successful",
    )
