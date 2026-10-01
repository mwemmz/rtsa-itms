from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import OFFICERS, require_role
from app.models.accident import Accident, AccidentSeverity, AccidentStatus, AccidentVehicle
from app.models.road_network import (
    IncidentSeverity,
    IncidentType,
    Road,
    RoadIncident,
    RoadSegment,
)
from app.models.user import User
from app.schemas.accident import (
    AccidentCreate,
    AccidentResponse,
    AccidentStats,
    AccidentVehicleResponse,
)
from app.schemas.fields import LOCATION_MAX_LENGTH
from app.services.audit import log_action
from app.services.notifications import broadcast
from app.services.routing import describe_place

router = APIRouter(prefix="/api/accidents", tags=["Accidents"])


def _with_road_state(db: Session, accidents: list[Accident]) -> list[AccidentResponse]:
    incident_ids = [a.incident_id for a in accidents if a.incident_id]
    active = set()
    if incident_ids:
        active = {
            i.id for i in db.query(RoadIncident.id).filter(
                RoadIncident.id.in_(incident_ids), RoadIncident.is_active == True  # noqa: E712
            )
        }
    out = []
    for a in accidents:
        item = AccidentResponse.model_validate(a)
        item.on_road = a.incident_id in active
        out.append(item)
    return out


@router.post("/", response_model=AccidentResponse, status_code=status.HTTP_201_CREATED)
def report_accident(
    payload: AccidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    # Only places on the mapped network are accepted, so every accident lands on
    # a real road that the status board and route planner know about.
    segment = None
    if payload.segment_id:
        segment = db.get(RoadSegment, payload.segment_id)
        if segment is None:
            raise HTTPException(status_code=422, detail="Unknown road stretch")
        if payload.road_id and payload.road_id != segment.road_id:
            raise HTTPException(status_code=422, detail="That stretch is not on the chosen road")
    road_id = segment.road_id if segment else payload.road_id
    if db.get(Road, road_id) is None:
        raise HTTPException(status_code=422, detail="Unknown road")

    road_name, stretch = describe_place(db, road_id, segment.id if segment else None)
    place = road_name + (f" ({stretch})" if stretch else "")
    if payload.location:
        place = f"{place}, {payload.location}"
    place = place[:LOCATION_MAX_LENGTH]

    accident = Accident(
        location=place,
        occurred_at=payload.occurred_at,
        severity=payload.severity,
        description=payload.description,
        road_id=road_id,
        segment_id=segment.id if segment else None,
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
        f"{payload.severity.value} accident at {place}", current_user.id
    )

    # An accident is a road incident too: it goes on the live alert feed, marks
    # the road on the status board and (via its segment) is routed around.
    incident = RoadIncident(
        incident_type=IncidentType.ACCIDENT,
        severity=IncidentSeverity(payload.severity.value),
        segment_id=accident.segment_id,
        road_id=road_id,
        description=payload.description or f"Accident at {place}",
        starts_at=payload.occurred_at,
        reported_by=current_user.id,
    )
    db.add(incident)
    db.flush()
    accident.incident_id = incident.id
    log_action(
        db, "report_incident", "road_incident", str(incident.id),
        f"{payload.severity.value} accident on {road_name}", current_user.id
    )
    broadcast(
        db,
        "road_alert",
        {
            "incident_type": "accident",
            "road": road_name + (f" ({stretch})" if stretch else ""),
            "description": payload.description or "",
            "suggestion": "This stretch is closed in the route planner. Plan an alternative route.",
        },
    )

    db.commit()
    db.refresh(accident)
    return _with_road_state(db, [accident])[0]


@router.get("/", response_model=list[AccidentResponse])
def list_accidents(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    accidents = db.query(Accident).order_by(Accident.occurred_at.desc()).limit(100).all()
    return _with_road_state(db, accidents)


@router.get("/stats", response_model=AccidentStats)
def accident_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
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
    current_user: User = Depends(require_role(*OFFICERS)),
):
    accident = db.query(Accident).filter(Accident.id == accident_id).first()
    if not accident:
        raise HTTPException(status_code=404, detail="Accident not found")
    return _with_road_state(db, [accident])[0]


@router.post("/{accident_id}/clear-road", response_model=AccidentResponse)
def clear_accident_from_road(
    accident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """The scene is cleared: reopen the stretch and drop it from the alert feed.

    The accident record itself stays for investigation and reporting.
    """
    accident = db.query(Accident).filter(Accident.id == accident_id).first()
    if not accident:
        raise HTTPException(status_code=404, detail="Accident not found")
    incident = db.get(RoadIncident, accident.incident_id) if accident.incident_id else None
    if incident is None or not incident.is_active:
        raise HTTPException(status_code=400, detail="This accident is not blocking the road")
    incident.is_active = False
    incident.ends_at = datetime.utcnow()
    db.flush()
    log_action(
        db, "resolve_incident", "road_incident", str(incident.id),
        f"Accident cleared at {accident.location}", current_user.id
    )
    log_action(db, "update", "accident", str(accident.id), "Cleared from road", current_user.id)
    db.commit()
    db.refresh(accident)
    return _with_road_state(db, [accident])[0]


@router.get("/{accident_id}/vehicles", response_model=list[AccidentVehicleResponse])
def get_accident_vehicles(
    accident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    return db.query(AccidentVehicle).filter(AccidentVehicle.accident_id == accident_id).all()
