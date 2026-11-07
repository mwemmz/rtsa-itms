import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import FIELD_STAFF, OFFICERS, require_role
from app.models.inspection import FitnessCertificate, Inspection, InspectionResult
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.inspection import (
    FitnessCertificateResponse,
    InspectionCreate,
    InspectionResponse,
    InspectionUpdate,
    ObdDecodeIn,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/inspections", tags=["Inspections"])


@router.post("/", response_model=InspectionResponse, status_code=status.HTTP_201_CREATED)
def schedule_inspection(
    payload: InspectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    vehicle = db.query(Vehicle).filter(Vehicle.id == payload.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    inspection = Inspection(
        vehicle_id=payload.vehicle_id,
        inspection_centre=payload.inspection_centre,
        scheduled_date=payload.scheduled_date,
    )
    db.add(inspection)
    db.flush()
    log_action(
        db, "schedule", "inspection", str(inspection.id),
        f"Scheduled inspection for {vehicle.registration_number}", current_user.id
    )
    db.commit()
    db.refresh(inspection)
    return inspection


@router.post("/obd/decode")
def decode_obd(payload: ObdDecodeIn, current_user: User = Depends(require_role(*FIELD_STAFF))):
    """Explain fault codes read from a vehicle's OBD-II port, and say which ones fail a roadworthiness test."""
    from app.services import inspection_rules

    try:
        return inspection_rules.decode_dtcs(payload.codes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/checklist-spec")
def checklist_spec(current_user: User = Depends(require_role(*FIELD_STAFF))):
    """The checklist items and instrument limits the test is judged against."""
    from app.services import inspection_rules

    return inspection_rules.spec()


@router.get("/vehicle/{vehicle_id}", response_model=list[InspectionResponse])
def list_inspections_for_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    return db.query(Inspection).filter(Inspection.vehicle_id == vehicle_id).order_by(Inspection.scheduled_date.desc()).all()


@router.patch("/{inspection_id}", response_model=InspectionResponse)
def update_inspection_result(
    inspection_id: str,
    payload: InspectionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    inspection = db.query(Inspection).filter(Inspection.id == inspection_id).first()
    if not inspection:
        raise HTTPException(status_code=404, detail="Inspection not found")

    from app.services import inspection_rules as rules

    data = payload.model_dump(exclude_unset=True)
    try:
        suggested, reasons = rules.evaluate(payload.checklist, payload.readings, payload.obd_codes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = payload.result
    if suggested is not None:
        if result is None:
            result = InspectionResult(suggested)  # the checks decide
        elif result == InspectionResult.PASSED and suggested == "failed":
            raise HTTPException(
                status_code=409,
                detail="Cannot pass this vehicle: " + "; ".join(reasons),
            )
        if result == InspectionResult.FAILED and not data.get("findings") and reasons:
            data["findings"] = "; ".join(reasons)
    data["result"] = result
    if payload.obd_codes is not None:
        data["obd_codes"] = rules.dump([d["code"] for d in rules.decode_dtcs(payload.obd_codes)])
    for key in ("checklist", "readings"):
        if key in data:
            data[key] = rules.dump(data[key])
    for field, value in data.items():
        if value is not None:
            setattr(inspection, field, value)
    if result is None:
        result = inspection.result

    # If passed, issue a fitness certificate
    if result == InspectionResult.PASSED:
        existing = db.query(FitnessCertificate).filter(
            FitnessCertificate.inspection_id == inspection.id
        ).first()
        if not existing:
            cert_number = f"FIT-{secrets.token_hex(4).upper()}"
            cert = FitnessCertificate(
                inspection_id=inspection.id,
                vehicle_id=inspection.vehicle_id,
                certificate_number=cert_number,
                issued_date=datetime.utcnow(),
                expiry_date=datetime.utcnow() + timedelta(days=365),
            )
            db.add(cert)

    db.flush()
    log_action(
        db, "update_result", "inspection", str(inspection.id),
        f"Result: {result.value}", current_user.id
    )
    db.commit()
    db.refresh(inspection)
    return inspection


@router.get("/{inspection_id}/fitness-certificate", response_model=FitnessCertificateResponse)
def get_fitness_certificate(
    inspection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    cert = db.query(FitnessCertificate).filter(
        FitnessCertificate.inspection_id == inspection_id
    ).first()
    if not cert:
        raise HTTPException(status_code=404, detail="No fitness certificate for this inspection")
    return cert


@router.get("/by-vehicle/{vehicle_id}/fitness-certificate", response_model=FitnessCertificateResponse)
def get_latest_fitness_certificate(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    cert = (
        db.query(FitnessCertificate)
        .filter(FitnessCertificate.vehicle_id == vehicle_id)
        .order_by(FitnessCertificate.issued_date.desc())
        .first()
    )
    if not cert:
        raise HTTPException(status_code=404, detail="No fitness certificate for this vehicle")
    return cert
