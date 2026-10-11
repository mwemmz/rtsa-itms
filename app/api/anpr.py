from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import FIELD_STAFF, require_role
from app.models.anpr import ANPREvent
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.anpr import ANPREventCreate, ANPREventResponse
from app.services.audit import log_action
from app.services.plates import find_vehicle_by_plate, normalise_plate, plate_alert, plate_key

router = APIRouter(prefix="/api/anpr", tags=["ANPR"])


def _with_alert(event: ANPREvent, decision: str, reasons: list[str]) -> dict:
    out = ANPREventResponse.model_validate(event).model_dump()
    out.update(decision=decision, reasons=reasons)
    return out


@router.post("/events", response_model=ANPREventResponse, status_code=status.HTTP_201_CREATED)
def capture_anpr_event(
    payload: ANPREventCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    vehicle = find_vehicle_by_plate(db, payload.plate_number)

    event = ANPREvent(
        # The registered form when the plate matched, so every read of one
        # vehicle is stored the same way; otherwise as read, tidied.
        plate_number=vehicle.registration_number if vehicle else " ".join(payload.plate_number.upper().split()),
        vehicle_id=vehicle.id if vehicle else None,
        location=payload.location,
        timestamp=payload.timestamp or datetime.utcnow(),
        confidence=payload.confidence,
        image_url=payload.image_url,
        camera_id=payload.camera_id,
    )
    db.add(event)
    db.flush()
    decision, reasons = plate_alert(db, vehicle)
    log_action(
        db, "capture", "anpr_event", str(event.id),
        f"Plate {event.plate_number} at {payload.location}: {decision}", current_user.id
    )
    db.commit()
    db.refresh(event)
    return _with_alert(event, decision, reasons)


@router.get("/events", response_model=list[ANPREventResponse])
def list_anpr_events(
    plate_number: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    query = db.query(ANPREvent)
    if plate_number:
        query = query.filter(plate_key(ANPREvent.plate_number) == normalise_plate(plate_number))
    events = query.order_by(ANPREvent.timestamp.desc()).limit(100).all()

    # The alert is the vehicle's state now, which is what an officer acting on
    # a sighting needs. Each vehicle is checked once however often it was seen.
    alerts: dict = {}
    out = []
    for event in events:
        if event.vehicle_id not in alerts:
            vehicle = db.get(Vehicle, event.vehicle_id) if event.vehicle_id else None
            alerts[event.vehicle_id] = plate_alert(db, vehicle)
        out.append(_with_alert(event, *alerts[event.vehicle_id]))
    return out
