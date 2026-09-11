import secrets
import uuid
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.inspection import FitnessCertificate, Inspection, InspectionResult
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.inspection import (
    FitnessCertificateResponse,
    InspectionCreate,
    InspectionResponse,
    InspectionUpdate,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/inspections", tags=["Inspections"])


@router.post("/", response_model=InspectionResponse, status_code=status.HTTP_201_CREATED)
def schedule_inspection(
    payload: InspectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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


@router.get("/vehicle/{vehicle_id}", response_model=list[InspectionResponse])
def list_inspections_for_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(inspection, field, value)

    # If passed, issue a fitness certificate
    if payload.result == InspectionResult.PASSED:
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
        f"Result: {payload.result}", current_user.id
    )
    db.commit()
    db.refresh(inspection)
    return inspection


@router.get("/{inspection_id}/fitness-certificate", response_model=FitnessCertificateResponse)
def get_fitness_certificate(
    inspection_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    current_user: User = Depends(get_current_user),
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
