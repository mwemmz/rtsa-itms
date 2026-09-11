from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.road_network import Intersection, RoadIncident
from app.schemas.routing import RouteOut, RouteResult
from app.services.audit import log_action
from app.services.routing import (
    active_blocking_segments,
    find_alternative_routes,
    find_route,
)

router = APIRouter(prefix="/api/routing", tags=["Routing"])


def _resolve_intersection(db: Session, ref: str) -> Intersection | None:
    try:
        return db.query(Intersection).filter(Intersection.id == ref).first()
    except Exception:
        return db.query(Intersection).filter(Intersection.name.ilike(f"%{ref}%")).first()


@router.get("/route", response_model=RouteResult)
def plan_route(
    from_: str,
    to: str,
    include_alternatives: bool = True,
    avoid_incidents: bool = True,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    origin = _resolve_intersection(db, from_)
    destination = _resolve_intersection(db, to)
    if not origin or not destination:
        raise HTTPException(status_code=404, detail="Origin or destination not found in the network")

    if avoid_incidents:
        avoided = len(active_blocking_segments(db))
    else:
        avoided = 0

    route = find_route(db, str(origin.id), str(destination.id), avoid_incidents=avoid_incidents)

    alternatives: list[RouteOut] = []
    if include_alternatives and route.steps:
        alt_routes = find_alternative_routes(
            db, str(origin.id), str(destination.id), avoid_incidents=avoid_incidents
        )
        for alt in alt_routes[1:]:
            if alt.steps:
                alternatives.append(
                    RouteOut(
                        total_distance_km=alt.total_distance_km,
                        total_minutes=alt.total_minutes,
                        step_count=alt.step_count,
                        steps=[
                            {
                                "from_intersection": s.from_intersection,
                                "to_intersection": s.to_intersection,
                                "road_name": s.road_name,
                                "distance_km": s.distance_km,
                                "travel_minutes": s.travel_minutes,
                            }
                            for s in alt.steps
                        ],
                    )
                )

    log_action(
        db, "route_query", "route",
        f"{origin.name}->{destination.name}",
        f"Primary {route.total_minutes}min, {len(alternatives)} alternative(s)",
        current_user.id,
    )

    primary = None
    if route.steps:
        primary = RouteOut(
            total_distance_km=route.total_distance_km,
            total_minutes=route.total_minutes,
            step_count=route.step_count,
            steps=[
                {
                    "from_intersection": s.from_intersection,
                    "to_intersection": s.to_intersection,
                    "road_name": s.road_name,
                    "distance_km": s.distance_km,
                    "travel_minutes": s.travel_minutes,
                }
                for s in route.steps
            ],
        )

    return RouteResult(
        origin=origin.name,
        destination=destination.name,
        primary_route=primary,
        alternatives=alternatives,
        incidents_avoided=avoided,
    )


@router.get("/status", response_model=list[dict])
def road_status_board(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Live board: which roads are open, congested, closed right now."""
    from app.models.road_network import Road, RoadSegment

    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .all()
    )
    blocked_segment_ids = active_blocking_segments(db)

    incidents_by_road: dict[str, list[str]] = {}
    for inc in incidents:
        key = str(inc.road_id) if inc.road_id else "none"
        incidents_by_road.setdefault(key, []).append(inc.incident_type.value)

    board = []
    for road in db.query(Road).all():
        key = str(road.id)
        segs = db.query(RoadSegment).filter(RoadSegment.road_id == road.id).all()
        blocked_segs = [s for s in segs if str(s.id) in blocked_segment_ids]
        if blocked_segs:
            status = "closed"
        elif key in incidents_by_road:
            status = "congested"
        else:
            status = road.status.value
        board.append(
            {
                "road": road.name,
                "class": road.road_class.value,
                "status": status,
                "active_incidents": incidents_by_road.get(key, []),
            }
        )
    return board