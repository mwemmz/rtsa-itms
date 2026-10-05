from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import OFFICERS, get_current_user, require_role
from app.core.timeutil import utcnow
from app.models.accident import Accident, AccidentSeverity
from app.models.user import User, UserRole
from app.models.road_network import (
    IncidentType,
    IncidentVerification,
    Intersection,
    Road,
    RoadIncident,
    RoadSegment,
)
from app.schemas.road_network import (
    IncidentCreate,
    IncidentDismiss,
    IncidentResponse,
    MyReport,
    ReporterStanding,
)
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import broadcast, notify
from app.services.road_reports import false_report_times, issue_false_report_fine, reporter_standing
from app.services.routing import active_blocking_segments, describe_place

router = APIRouter(prefix="/api/incidents", tags=["Incidents"])

TYPE_LABELS = {
    IncidentType.ACCIDENT: "Accident",
    IncidentType.ROAD_CLOSED: "Road closed",
    IncidentType.MAINTENANCE: "Maintenance",
    IncidentType.CONGESTION: "Congestion",
    IncidentType.ROADWORKS: "Roadworks",
}


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


def _place(db: Session, incident: RoadIncident) -> str:
    road_name, stretch = describe_place(db, incident.road_id, incident.segment_id)
    return (road_name or "a Lusaka road") + (f" ({stretch})" if stretch else "")


def _broadcast_road_alert(db: Session, incident: RoadIncident) -> None:
    broadcast(
        db,
        "road_alert",
        {
            "incident_type": incident.incident_type.value,
            "road": _place(db, incident),
            "description": incident.description or "",
            "suggestion": _suggest_alternative(db, incident) or "Plan an alternative route.",
        },
    )


@router.post("/", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
def report_incident(
    payload: IncidentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Officers and admins publish official alerts; citizens report what they see.

    A citizen's report goes on the live feed and closes its stretch in the route
    planner straight away, marked unverified until an officer reviews it.
    """
    if current_user.role == UserRole.TOLL_OPERATOR:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    citizen = current_user.role == UserRole.CITIZEN
    if citizen:
        standing = reporter_standing(db, current_user)
        if not standing.can_report:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=standing.reason)
        if not payload.declaration:
            raise HTTPException(
                status_code=422,
                detail="Confirm the report is true. A report found false is fined to your NRC.",
            )

    # Incidents must sit on the mapped network, or the status board can't mark
    # the road and the route planner can't avoid it.
    if payload.segment_id is None and payload.road_id is None:
        raise HTTPException(status_code=422, detail="Choose the road (and stretch) the incident is on")
    if citizen and payload.segment_id is None:
        # Someone on the spot knows which stretch; "Use my location" finds it.
        raise HTTPException(status_code=422, detail="Choose the stretch between junctions where it is")
    road_id = payload.road_id
    if payload.segment_id is not None:
        seg = db.get(RoadSegment, payload.segment_id)
        if seg is None:
            raise HTTPException(status_code=422, detail="Unknown road stretch")
        if road_id is not None and road_id != seg.road_id:
            raise HTTPException(status_code=422, detail="That stretch is not on the chosen road")
        road_id = seg.road_id
    if db.get(Road, road_id) is None:
        raise HTTPException(status_code=422, detail="Unknown road")

    if citizen:
        already = (
            db.query(RoadIncident)
            .filter(
                RoadIncident.is_active == True,  # noqa: E712
                RoadIncident.segment_id == payload.segment_id,
                RoadIncident.incident_type == payload.incident_type,
            )
            .first()
        )
        if already:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This is already on the road feed and the route planner is avoiding it. Thanks for checking.",
            )

    incident = RoadIncident(
        incident_type=payload.incident_type,
        severity=payload.severity,
        segment_id=payload.segment_id,
        road_id=road_id,
        description=(payload.description or "").strip() or None,
        reported_by=current_user.id,
        verification=(IncidentVerification.UNVERIFIED if citizen else IncidentVerification.OFFICIAL).value,
    )
    db.add(incident)
    db.flush()
    place = _place(db, incident)

    if citizen:
        # Motorists get pushed alerts once an officer confirms it; officers hear now.
        broadcast(
            db,
            "road_report_received",
            {"incident_type": TYPE_LABELS[incident.incident_type], "place": place,
             "severity": incident.severity.value, "reporter": current_user.full_name},
            roles=[UserRole.OFFICER.value, UserRole.ADMIN.value],
        )
    elif payload.broadcast_alert:
        _broadcast_road_alert(db, incident)

    log_action(
        db, "report_incident", "road_incident", str(incident.id),
        f"{payload.severity.value} {payload.incident_type.value} on {place}"
        + (" (citizen report, unverified)" if citizen else ""),
        current_user.id,
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


@router.get("/reporting-status", response_model=ReporterStanding)
def reporting_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Whether the signed-in user can report right now, and the rules that apply."""
    return reporter_standing(db, current_user).as_dict()


@router.get("/mine", response_model=list[MyReport])
def my_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The signed-in user's own reports, newest first, with how each was reviewed."""
    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.reported_by == current_user.id)
        .order_by(RoadIncident.starts_at.desc())
        .limit(20)
        .all()
    )
    out = []
    for inc in incidents:
        item = MyReport.model_validate(inc)
        item.road_name, item.stretch = describe_place(db, inc.road_id, inc.segment_id)
        out.append(item)
    return out


@router.get("/alerts", response_model=list[dict])
def active_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live alert feed for motorists: active incidents + reroute hints.

    Officers and admins also see who made each citizen report (name, NRC and
    their recent false reports) so they can weigh it before reviewing.
    """
    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .order_by(RoadIncident.starts_at.desc())
        .all()
    )
    reviewer = current_user.role in OFFICERS
    since = utcnow() - timedelta(days=runtime_settings.get(db, "road_reports.strike_window_days"))
    reporters: dict = {}
    results = []
    for inc in incidents:
        road_name, stretch = describe_place(db, inc.road_id, inc.segment_id)
        citizen_report = inc.verification != IncidentVerification.OFFICIAL.value
        item = {
            "id": str(inc.id),
            "incident_type": inc.incident_type.value,
            "severity": inc.severity.value,
            "road": road_name,
            "stretch": stretch,
            "segment_id": str(inc.segment_id) if inc.segment_id else None,
            "blocking": bool(inc.segment_id) and inc.incident_type.value in ("accident", "road_closed"),
            "description": inc.description,
            "started_at": inc.starts_at.isoformat() if inc.starts_at else None,
            "suggestion": _suggest_alternative(db, inc),
            "verification": inc.verification,
            "citizen_report": citizen_report,
            "mine": inc.reported_by == current_user.id,
        }
        if reviewer and citizen_report and inc.reported_by:
            if inc.reported_by not in reporters:
                user = db.get(User, inc.reported_by)
                reporters[inc.reported_by] = user and {
                    "name": user.full_name,
                    "nrc": user.nrc_number,
                    "phone": user.phone_number,
                    "false_reports": len(false_report_times(db, user.id, since)),
                }
            item["reporter"] = reporters[inc.reported_by]
        results.append(item)
    return results


def _report_awaiting_review(db: Session, incident_id: str) -> RoadIncident:
    incident = db.query(RoadIncident).filter(RoadIncident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if incident.verification != IncidentVerification.UNVERIFIED.value:
        raise HTTPException(status_code=409, detail="Only unverified citizen reports can be reviewed")
    return incident


def _naive_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.utcnow()
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


@router.post("/{incident_id}/confirm", response_model=IncidentResponse)
def confirm_report(
    incident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """An officer confirms a citizen's report: it becomes an official alert.

    Motorists are alerted now, and a confirmed accident opens an accident case
    linked to this incident, so "Clear from road" on the Accidents screen reopens it.
    """
    incident = _report_awaiting_review(db, incident_id)
    incident.verification = IncidentVerification.CONFIRMED.value
    incident.reviewed_by = current_user.id
    incident.reviewed_at = utcnow()
    place = _place(db, incident)

    if incident.incident_type == IncidentType.ACCIDENT:
        accident = Accident(
            location=place[:200],
            occurred_at=_naive_utc(incident.starts_at),
            severity=AccidentSeverity(incident.severity.value),
            description=incident.description,
            road_id=incident.road_id,
            segment_id=incident.segment_id,
            incident_id=incident.id,
            reported_by=incident.reported_by,
        )
        db.add(accident)
        db.flush()
        log_action(db, "report", "accident", str(accident.id),
                   f"{incident.severity.value} accident at {place} (confirmed citizen report)", current_user.id)

    db.flush()
    log_action(db, "confirm_incident", "road_incident", str(incident.id),
               f"Confirmed citizen report: {incident.incident_type.value} on {place}", current_user.id)
    _broadcast_road_alert(db, incident)
    if incident.reported_by:
        notify(db, incident.reported_by, "road_report_confirmed",
               {"incident_type": TYPE_LABELS[incident.incident_type], "place": place})
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/{incident_id}/dismiss", response_model=IncidentResponse)
def dismiss_report(
    incident_id: str,
    payload: IncidentDismiss,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Take a citizen's report off the road feed (the stretch reopens).

    ``false_report`` is for a report the officer found to be untrue: the reporter
    is fined and it counts towards a reporting suspension. Otherwise (already
    cleared, a duplicate, can't be confirmed) nobody is blamed.
    """
    incident = _report_awaiting_review(db, incident_id)
    incident.is_active = False
    incident.ends_at = datetime.utcnow()
    incident.verification = (
        IncidentVerification.FALSE if payload.false_report else IncidentVerification.DISMISSED
    ).value
    incident.review_note = payload.reason
    incident.reviewed_by = current_user.id
    incident.reviewed_at = utcnow()
    db.flush()
    place = _place(db, incident)
    log_action(
        db, "dismiss_incident", "road_incident", str(incident.id),
        f"{'False report' if payload.false_report else 'Dismissed report'} on {place}: {payload.reason}",
        current_user.id,
    )
    if payload.false_report:
        issue_false_report_fine(db, incident, place, current_user)
    elif incident.reported_by:
        notify(db, incident.reported_by, "road_report_dismissed",
               {"incident_type": TYPE_LABELS[incident.incident_type], "place": place,
                "reason": payload.reason})
    db.commit()
    db.refresh(incident)
    return incident


@router.post("/{incident_id}/resolve", response_model=IncidentResponse)
def resolve_incident(
    incident_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.ADMIN)),
):
    incident = db.query(RoadIncident).filter(RoadIncident.id == incident_id).first()
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    if not incident.is_active:
        raise HTTPException(status_code=400, detail="Incident is already resolved")
    if incident.verification == IncidentVerification.UNVERIFIED.value:
        # Resolving would hide whether the report was true; make the call first.
        raise HTTPException(
            status_code=409,
            detail="Review this citizen report first: confirm it, dismiss it or mark it false",
        )
    incident.is_active = False
    incident.ends_at = datetime.utcnow()
    db.flush()
    log_action(db, "resolve_incident", "road_incident", str(incident.id), "Resolved", current_user.id)
    db.commit()
    db.refresh(incident)
    return incident
