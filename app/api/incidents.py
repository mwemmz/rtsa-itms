from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user, require_role
from app.models.user import User, UserRole
from app.models.road_network import (
    Intersection,
    Road,
    RoadIncident,
    RoadSegment,
)
from app.schemas.road_network import IncidentCreate, IncidentResponse
from app.services.audit import log_action
from app.services.notifications import broadcast
from app.services.routing import active_blocking_segments

router = APIRouter(prefix="/api/incidents", tags=["Incidents"])


def _suggest_alternative(db: Session, incident: RoadIncident) -> str | None:
    """Produce a plain-text reroute hint for the affected road, if possible."""
    if incident.segment_id:
        seg = db.query(RoadSegment).filter(RoadSegment.id == incident.segment_id).first()
        if seg:
            inter_start = db.query(Intersection).filter(Intersection.id == seg.start_intersection_id).first()
            inter_end = db.query(Intersection).filter(Intersection.id == seg.end_intersection_id).first()
            if inter_start and inter_end:
                road = db.query(Road).filter(Road.id == seg.road_id).first()
                road_name = road.name if road else "the affected road"
                return (
                    f"{road_name} is blocked between {inter_start.name} and {inter_end.name}. "
                    "Use the route planner (/api/routing/route) for an alternative."
                )
    if incident.road_id:
        road = db.query(Road).filter(Road.id == incident.road_id).first()
        if road:
            return f"{road.name} is affected. Check /api/routing/route for alternatives."
    return None


def _affected_blocked_count(db: Session) -> int:
    return sum(1 for _ in active_blocking_segments(db))


@router.post("/", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
def report_incident(
    payload: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    incident = RoadIncident(
        incident_type=payload.incident_type,
        severity=payload.severity,
        segment_id=payload.segment_id,
        road_id=payload.road_id,
        description=payload.description,
        reported_by=current_user.id,
    )
    db.add(incident)
    db.flush()

    suggestion = _suggest_alternative(db, incident)

    # Notify motorists
    if payload.broadcast_alert:
        road_name = None
        if incident.road_id:
            road = db.query(Road).filter(Road.id == incident.road_id).first()
            road_name = road.name if road else None
        broadcast(
            db,
            "road_alert",
            {
                "incident_type": payload.incident_type.value,
                "road": road_name or "a Lusaka road",
                "description": payload.description or "",
                "suggestion": suggestion or "Plan an alternative route.",
            },
        )

    log_action(
        db, "report_incident", "road_incident", str(incident.id),
        f"{payload.severity.value} {payload.incident_type.value} on {road_name or 'unknown road'}", current_user.id
    )
    db.commit()
    db.refresh(incident)
    return incident


@router.get("/", response_model=list[IncidentResponse])
def list_incidents(
    active_only: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(RoadIncident).order_by(RoadIncident.starts_at.desc())
    if active_only:
        query = query.filter(RoadIncident.is_active == True)
    return query.all()


@router.get("/alerts", response_model=list[dict])
def active_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live alert feed for motorists: active incidents + reroute hints."""
    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .order_by(RoadIncident.starts_at.desc())
        .all()
    )
    results = []
    for inc in incidents:
        road_name = None
        if inc.road_id:
            road = db.query(Road).filter(Road.id == inc.road_id).first()
            road_name = road.name if road else None
        results.append(
            {
                "id": str(inc.id),
                "incident_type": inc.incident_type.value,
                "severity": inc.severity.value,
                "road": road_name,
                "description": inc.description,
                "started_at": inc.starts_at.isoformat() if inc.starts_at else None,
                "suggestion": _suggest_alternative(db, inc),
            }
        )
    return results


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    incident = db.query(RoadIncident).filter(RoadIncident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    incident.is_active = False
    incident.ends_at = datetime.utcnow()
    db.flush()
    log_action(db, "resolve_incident", "road_incident", str(incident.id), "Resolved", current_user.id)
    db.commit()
    db.refresh(incident)
    return incident