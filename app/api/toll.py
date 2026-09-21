import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.api.enforcement import generate_challan_reference
from app.models.enforcement import Challan, ChallanStatus, Violation, ViolationType
from app.models.toll import TollComplianceResult, TollTransaction
from app.models.toll_offline import OfflineTollEvent, OfflineTollEventStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.toll import (
    ComplianceResponse,
    OfflineTollEventCreate,
    OfflineTollSyncRequest,
    OfflineTollSyncResponse,
    TollEventCreate,
    TollTransactionResponse,
)
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


def _process_toll_event(
    db: Session,
    plate: str,
    gate_id: str,
    timestamp: datetime | None,
    toll_amount: int | None,
    actor: User,
) -> ComplianceResponse:
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
        gate_id=gate_id,
        timestamp=timestamp or datetime.now(timezone.utc),
        compliance_result=result,
        flagged_issues="; ".join(compliance.issues) if compliance.issues else None,
        toll_amount=toll_amount,
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
            location=f"Toll gate {gate_id}",
            description="; ".join(compliance.issues),
            recorded_by=actor.id,
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
        f"{plate} at {gate_id}: {result.value}", actor.id
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
                    "gate_id": gate_id,
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


@router.post("/events", response_model=ComplianceResponse, status_code=status.HTTP_201_CREATED)
def process_toll_gate_event(
    payload: TollEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    response = _process_toll_event(
        db, payload.plate_number.upper(), payload.gate_id, payload.timestamp, payload.toll_amount, current_user
    )
    db.commit()
    return response


@router.post("/offline/events", status_code=status.HTTP_201_CREATED)
def queue_offline_toll_event(
    payload: OfflineTollEventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("officer", "toll_operator", "admin")),
):
    existing = db.query(OfflineTollEvent).filter(
        OfflineTollEvent.device_event_id == payload.device_event_id
    ).first()
    if existing:
        return {"id": existing.id, "status": existing.status, "duplicate": True}

    event = OfflineTollEvent(
        device_event_id=payload.device_event_id,
        plate_number=payload.plate_number.upper(),
        gate_id=payload.gate_id,
        occurred_at=payload.occurred_at,
        toll_amount=payload.toll_amount,
        cached_compliance_result=payload.cached_compliance_result.value if payload.cached_compliance_result else None,
        cached_issues=json.dumps(payload.cached_issues),
        cached_checks=json.dumps(payload.cached_checks),
        received_by=current_user.id,
    )
    db.add(event)
    log_action(db, "queue_toll_event", "toll_offline_event", str(event.id),
               f"Queued {event.plate_number} from device event {event.device_event_id}", current_user.id)
    db.commit()
    db.refresh(event)
    return {"id": event.id, "status": event.status, "duplicate": False}


@router.post("/offline/sync", response_model=OfflineTollSyncResponse)
def sync_offline_toll_events(
    payload: OfflineTollSyncRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role("officer", "toll_operator", "admin")),
):
    query = db.query(OfflineTollEvent).filter(OfflineTollEvent.status == OfflineTollEventStatus.QUEUED)
    if payload.event_ids:
        query = query.filter(OfflineTollEvent.id.in_(payload.event_ids))
    if payload.gate_id:
        query = query.filter(OfflineTollEvent.gate_id == payload.gate_id)
    events = query.order_by(OfflineTollEvent.occurred_at.asc()).limit(payload.limit).all()
    transactions = []
    rejected = 0
    for event in events:
        try:
            result = _process_toll_event(
                db, event.plate_number, event.gate_id, event.occurred_at, event.toll_amount, current_user
            )
            event.status = OfflineTollEventStatus.SYNCED
            event.synced_transaction_id = result.transaction.id
            event.synced_at = datetime.now(timezone.utc)
            transactions.append(result.transaction)
            log_action(db, "sync_toll_event", "toll_offline_event", str(event.id),
                       f"Synchronized device event {event.device_event_id}", current_user.id)
        except HTTPException as exc:
            event.status = OfflineTollEventStatus.REJECTED
            event.sync_error = str(exc.detail)[:300]
            rejected += 1
            log_action(db, "reject_toll_event", "toll_offline_event", str(event.id),
                       event.sync_error, current_user.id)
    db.commit()
    return OfflineTollSyncResponse(
        queued=len(events), synced=len(transactions), rejected=rejected, transactions=transactions
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