from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.road_network import (
    Intersection,
    Road,
    RoadIncident,
    RoadSegment,
)
from app.schemas.road_network import (
    IntersectionCreate,
    IntersectionResponse,
    RoadCreate,
    RoadResponse,
    SegmentCreate,
    SegmentResponse,
)
from app.services.audit import log_action

router = APIRouter(prefix="/api/road-network", tags=["Road Network"])


@router.post("/roads", response_model=RoadResponse, status_code=status.HTTP_201_CREATED)
def create_road(
    payload: RoadCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    existing = db.query(Road).filter(Road.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Road already exists")
    road = Road(**payload.model_dump())
    db.add(road)
    db.flush()
    log_action(db, "create", "road", str(road.id), road.name, current_user.id)
    db.commit()
    db.refresh(road)
    return road


@router.get("/roads", response_model=list[RoadResponse])
def list_roads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Road).order_by(Road.name).all()


@router.post("/intersections", response_model=IntersectionResponse, status_code=status.HTTP_201_CREATED)
def create_intersection(
    payload: IntersectionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    intersection = Intersection(**payload.model_dump())
    db.add(intersection)
    db.flush()
    log_action(db, "create", "intersection", str(intersection.id), intersection.name, current_user.id)
    db.commit()
    db.refresh(intersection)
    return intersection


@router.get("/intersections", response_model=list[IntersectionResponse])
def list_intersections(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(Intersection).all()


@router.post("/segments", response_model=SegmentResponse, status_code=status.HTTP_201_CREATED)
def create_segment(
    payload: SegmentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    seg = RoadSegment(**payload.model_dump())
    db.add(seg)
    db.flush()
    log_action(db, "create", "road_segment", str(seg.id), "Added segment", current_user.id)
    db.commit()
    db.refresh(seg)
    return seg


@router.get("/segments", response_model=list[SegmentResponse])
def list_segments(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return db.query(RoadSegment).all()


@router.get("/geojson")
def roads_geojson(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """GeoJSON FeatureCollection of Lusaka roads for map rendering (leaflet-style)."""
    intersections = {str(i.id): i for i in db.query(Intersection).all()}
    segments = db.query(RoadSegment).all()
    road_names = {str(r.id): r.name for r in db.query(Road).all()}

    features = []
    for seg in segments:
        start = intersections.get(str(seg.start_intersection_id))
        end = intersections.get(str(seg.end_intersection_id))
        if not start or not end:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": str(seg.id),
                    "road": road_names.get(str(seg.road_id), "Unknown"),
                    "distance_km": seg.distance_km,
                    "travel_minutes": seg.travel_minutes,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [start.longitude, start.latitude],
                        [end.longitude, end.latitude],
                    ],
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


@router.get("/leaflet")
def road_leaflet(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Human-readable 'leaflet' of the Lusaka road network with live status."""
    roads = db.query(Road).order_by(Road.name).all()
    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .all()
    )
    incident_roads = {str(i.road_id) for i in incidents if i.road_id}
    entries = []
    for road in roads:
        dings = len([i for i in incidents if i.road_id and str(i.road_id) == str(road.id)])
        entries.append(
            {
                "road": road.name,
                "class": road.road_class.value,
                "status": road.status.value,
                "active_incidents": dings,
            }
        )
    alerts = [
        {
            "type": i.incident_type.value,
            "severity": i.severity.value,
            "description": i.description,
            "active": i.is_active,
        }
        for i in incidents
    ]
    return {"network_name": "Lusaka Road Network", "roads": entries, "active_alerts": alerts}