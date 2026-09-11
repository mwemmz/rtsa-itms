from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.accident import Accident, AccidentSeverity, AccidentStatus, AccidentVehicle
from app.models.user import User
from app.schemas.accident import (
    AccidentCreate,
    AccidentResponse,
    AccidentStats,
    AccidentVehicleResponse,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/accidents", tags=["Accidents"])


@router.post("/", response_model=AccidentResponse, status_code=status.HTTP_201_CREATED)
def report_accident(
    payload: AccidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    accident = Accident(
        location=payload.location,
        occurred_at=payload.occurred_at,
        severity=payload.severity,
        description=payload.description,
        reported_by=current_user.id,
    )
    db.add(accident)
    db.flush()

    for vehicle_data in payload.vehicles:
        db.add(
            AccidentVehicle(
                accident_id=accident.id,
                vehicle_id=vehicle_data.vehicle_id,
                driver_id=vehicle_data.driver_id,
                plate_number=vehicle_data.plate_number.upper(),
                role=vehicle_data.role,
            )
        )

    db.flush()
    log_action(
        db, "report", "accident", str(accident.id),
        f"{payload.severity.value} accident at {payload.location}", current_user.id
    )
    db.commit()
    db.refresh(accident)
    return accident


@router.get("/", response_model=list[AccidentResponse])
def list_accidents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Accident).order_by(Accident.occurred_at.desc()).limit(100).all()


@router.get("/stats", response_model=AccidentStats)
def accident_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    total = db.query(Accident).count()
    by_severity = {}
    by_status = {}
    for severity in AccidentSeverity:
        by_severity[severity.value] = db.query(Accident).filter(Accident.severity == severity).count()
    for a_status in AccidentStatus:
        by_status[a_status.value] = db.query(Accident).filter(Accident.status == a_status).count()
    return AccidentStats(total=total, by_severity=by_severity, by_status=by_status)


@router.get("/{accident_id}", response_model=AccidentResponse)
def get_accident(
    accident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    accident = db.query(Accident).filter(Accident.id == accident_id).first()
    if not accident:
        raise HTTPException(status_code=404, detail="Accident not found")
    return accident


@router.get("/{accident_id}/vehicles", response_model=list[AccidentVehicleResponse])
def get_accident_vehicles(
    accident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(AccidentVehicle).filter(AccidentVehicle.accident_id == accident_id).all()