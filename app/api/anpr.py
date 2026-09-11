from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.anpr import ANPREvent
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.anpr import ANPREventCreate, ANPREventResponse
from app.services.audit import log_action

router = APIRouter(prefix="/api/anpr", tags=["ANPR"])


@router.post("/events", response_model=ANPREventResponse, status_code=status.HTTP_201_CREATED)
def capture_anpr_event(
    payload: ANPREventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicle = db.query(Vehicle).filter(
        Vehicle.registration_number == payload.plate_number
    ).first()

    event = ANPREvent(
        plate_number=payload.plate_number.upper(),
        vehicle_id=vehicle.id if vehicle else None,
        location=payload.location,
        timestamp=payload.timestamp,
        confidence=payload.confidence,
        image_url=payload.image_url,
        camera_id=payload.camera_id,
    )
    db.add(event)
    db.flush()
    log_action(
        db, "capture", "anpr_event", str(event.id),
        f"Plate {payload.plate_number} at {payload.location}", current_user.id
    )
    db.commit()
    db.refresh(event)
    return event


@router.get("/events", response_model=list[ANPREventResponse])
def list_anpr_events(
    plate_number: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(ANPREvent)
    if plate_number:
        query = query.filter(ANPREvent.plate_number == plate_number.upper())
    return query.order_by(ANPREvent.timestamp.desc()).limit(100).all()
