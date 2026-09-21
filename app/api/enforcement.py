import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.enforcement import (
    ChallanPaymentResult,
    ChallanResponse,
    ViolationCreate,
    ViolationResponse,
)
from app.models.payment import PaymentType
from app.services.audit import log_action
from app.services.payments import create_payment
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


def _enrich_violations(db: Session, violations: list[Violation]) -> list[dict]:
    """Attach offender identity (vehicle plate + owner, driver name) to violations."""
    vehicle_ids = list({v.vehicle_id for v in violations if v.vehicle_id})
    driver_ids = list({v.driver_id for v in violations if v.driver_id})
    vehicles = (
        {str(x.id): x for x in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all()}
        if vehicle_ids
        else {}
    )
    drivers = (
        {str(x.id): x for x in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
        if driver_ids
        else {}
    )

    out = []
    for v in violations:
        veh = vehicles.get(str(v.vehicle_id)) if v.vehicle_id else None
        drv = drivers.get(str(v.driver_id)) if v.driver_id else None
        out.append(
            {
                "id": v.id,
                "vehicle_id": v.vehicle_id,
                "driver_id": v.driver_id,
                "violation_type": v.violation_type,
                "location": v.location,
                "timestamp": v.timestamp,
                "description": v.description,
                "registration_number": veh.registration_number if veh else None,
                "owner_name": veh.owner_name if veh else None,
                "owner_id_number": veh.owner_id_number if veh else None,
                "driver_name": f"{drv.first_name} {drv.last_name}".strip() if drv else None,
            }
        )
    return out


def _enrich_challans(db: Session, challans: list[Challan]) -> list[dict]:
    """Attach offender identity to challans so dashboards identify who is charged."""
    vehicle_ids = list({c.vehicle_id for c in challans if c.vehicle_id})
    driver_ids = list({c.driver_id for c in challans if c.driver_id})
    vehicles = (
        {str(x.id): x for x in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all()}
        if vehicle_ids
        else {}
    )
    drivers = (
        {str(x.id): x for x in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
        if driver_ids
        else {}
    )

    out = []
    for c in challans:
        veh = vehicles.get(str(c.vehicle_id)) if c.vehicle_id else None
        drv = drivers.get(str(c.driver_id)) if c.driver_id else None
        out.append(
            {
                "id": c.id,
                "reference": c.reference,
                "violation_id": c.violation_id,
                "vehicle_id": c.vehicle_id,
                "driver_id": c.driver_id,
                "penalty_amount": c.penalty_amount,
                "due_date": c.due_date,
                "status": c.status,
                "created_at": c.created_at,
                "registration_number": veh.registration_number if veh else None,
                "owner_name": veh.owner_name if veh else None,
                "driver_name": f"{drv.first_name} {drv.last_name}".strip() if drv else None,
            }
        )
    return out


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
    violations = query.order_by(Violation.timestamp.desc()).offset(skip).limit(limit).all()
    return _enrich_violations(db, violations)


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
    challans = query.order_by(Challan.created_at.desc()).offset(skip).limit(limit).all()
    return _enrich_challans(db, challans)


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

    # Settlement (ownership check, ledger, receipt, notification) lives in the payments service.
    create_payment(db, current_user, PaymentType.FINE, challan.id)
    db.commit()
    db.refresh(challan)
    return ChallanPaymentResult(
        challan_id=challan.id,
        reference=challan.reference,
        status=challan.status,
        message="Payment successful",
    )
