from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.api.enforcement import generate_challan_reference
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
from app.models.toll import TollComplianceResult, TollTransaction
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.toll import ComplianceResponse, TollEventCreate, TollTransactionResponse
from app.services.audit import log_action
from app.services.compliance import check_vehicle_compliance
from app.services.notifications import notify

router = APIRouter(prefix="/api/toll", tags=["Toll"])

FLAGGED_VIOLATION_MAP = {
    "registration status": ViolationType.OTHER,
    "Vehicle status": ViolationType.OTHER,
    "blacklisted": ViolationType.BLACKLISTED_VEHICLE,
    "insurance": ViolationType.NO_INSURANCE,
    "fitness": ViolationType.EXPIRED_FITNESS,
    "outstanding": ViolationType.OTHER,
}


@router.post("/events", response_model=ComplianceResponse, status_code=status.HTTP_201_CREATED)
def process_toll_gate_event(
    payload: TollEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plate = payload.plate_number.upper()
    vehicle = db.query(Vehicle).filter(Vehicle.registration_number == plate).first()

    # Check if vehicle exists
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No vehicle registered with plate {plate}",
        )

    compliance = check_vehicle_compliance(db, vehicle)
    result = TollComplianceResult.COMPLIANT if compliance.compliant else TollComplianceResult.FLAGGED

    transaction = TollTransaction(
        vehicle_id=vehicle.id,
        plate_number=plate,
        gate_id=payload.gate_id,
        compliance_result=result,
        flagged_issues="; ".join(compliance.issues) if compliance.issues else None,
        toll_amount=payload.toll_amount,
        is_paid=compliance.compliant,
    )
    db.add(transaction)
    db.flush()

    challan_created = False
    # Auto-generate e-Challan for flagged cases
    if result == TollComplianceResult.FLAGGED:
        violation_type = ViolationType.BLACKLISTED_VEHICLE if vehicle.is_blacklisted else ViolationType.OTHER
        violation = Violation(
            vehicle_id=vehicle.id,
            violation_type=violation_type,
            location=f"Toll gate {payload.gate_id}",
            description="; ".join(compliance.issues),
            recorded_by=current_user.id,
        )
        db.add(violation)
        db.flush()

        challan = Challan(
            violation_id=violation.id,
            vehicle_id=vehicle.id,
            penalty_amount=2000000 if vehicle.is_blacklisted else 100000,
            due_date=datetime.utcnow() + timedelta(days=14),
            status=ChallanStatus.UNPAID,
            reference=generate_challan_reference(),
        )
        db.add(challan)
        challan_created = True

    log_action(
        db, "toll_event", "toll_transaction", str(transaction.id),
        f"{plate} at {payload.gate_id}: {result.value}", current_user.id
    )

    if result == TollComplianceResult.FLAGGED:
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
                "toll_flagged",
                {
                    "registration": plate,
                    "gate_id": payload.gate_id,
                    "issues": "; ".join(compliance.issues),
                },
            )

    db.commit()
    db.refresh(transaction)

    txn_data = TollTransactionResponse.model_validate(transaction)

    return ComplianceResponse(
        vehicle_id=vehicle.id,
        plate_number=plate,
        compliance_result=result,
        checks=compliance.checks,
        flagged_issues=compliance.issues,
        challan_created=challan_created,
        transaction=txn_data,
    )


@router.get("/transactions", response_model=list[TollTransactionResponse])
def list_toll_transactions(
    plate_number: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(TollTransaction)
    if plate_number:
        query = query.filter(TollTransaction.plate_number == plate_number.upper())
    return query.order_by(TollTransaction.timestamp.desc()).limit(100).all()