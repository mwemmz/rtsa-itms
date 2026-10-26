import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import FIELD_STAFF, OFFICERS, require_role
from app.models.psv import PSVCorridor, PSVOperator, PSVPermit, PSVPermitStatus, PSVRouteAlert
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.psv import (
    PSVOperatorCreate,
    PSVOperatorResponse,
    PSVCorridorResponse,
    PSVCorridorSet,
    PSVPermitCreate,
    PSVPermitResponse,
    PSVPositionReport,
    PSVPositionResult,
    PSVRouteAlertResponse,
)
from app.services.audit import log_action
from app.services.geofence import distance_to_corridor_m
from app.services.notifications import broadcast

router = APIRouter(prefix="/api/psv", tags=["PSV"])


@router.post("/operators", response_model=PSVOperatorResponse, status_code=status.HTTP_201_CREATED)
def register_operator(
    payload: PSVOperatorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    existing = db.query(PSVOperator).filter(
        PSVOperator.licence_number == payload.licence_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Operator licence already exists")

    operator = PSVOperator(**payload.model_dump())
    db.add(operator)
    db.flush()
    log_action(db, "register", "psv_operator", str(operator.id), f"Registered {operator.name}", current_user.id)
    db.commit()
    db.refresh(operator)
    return operator


@router.get("/operators", response_model=list[PSVOperatorResponse])
def list_operators(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    return db.query(PSVOperator).all()


@router.get("/operators/{operator_id}", response_model=PSVOperatorResponse)
def get_operator(
    operator_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    operator = db.query(PSVOperator).filter(PSVOperator.id == operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")
    return operator


@router.post("/permits", response_model=PSVPermitResponse, status_code=status.HTTP_201_CREATED)
def issue_permit(
    payload: PSVPermitCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    operator = db.query(PSVOperator).filter(PSVOperator.id == payload.operator_id).first()
    if not operator:
        raise HTTPException(status_code=404, detail="Operator not found")

    vehicle = db.query(Vehicle).filter(Vehicle.id == payload.vehicle_id).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")

    existing = db.query(PSVPermit).filter(
        PSVPermit.permit_number == payload.permit_number
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Permit number already exists")

    permit = PSVPermit(**payload.model_dump())
    db.add(permit)
    db.flush()
    log_action(
        db, "issue_permit", "psv_permit", str(permit.id),
        f"Permit {permit.permit_number} for {vehicle.registration_number}", current_user.id
    )
    db.commit()
    db.refresh(permit)
    return permit


@router.get("/permits", response_model=list[PSVPermitResponse])
def list_permits(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    return db.query(PSVPermit).all()


@router.get("/permits/vehicle/{vehicle_id}", response_model=PSVPermitResponse)
def get_active_permit_for_vehicle(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    permit = (
        db.query(PSVPermit)
        .filter(
            PSVPermit.vehicle_id == vehicle_id,
            PSVPermit.status == PSVPermitStatus.ACTIVE,
            PSVPermit.expiry_date >= datetime.utcnow(),
        )
        .first()
    )
    if not permit:
        raise HTTPException(status_code=404, detail="No active permit for this vehicle")
    return permit


# --- Geo-fencing: route corridors and position checks --------------------------------

ALERT_DEDUPE_MINUTES = 10


def _corridor_response(corridor: PSVCorridor) -> PSVCorridorResponse:
    return PSVCorridorResponse(
        permit_id=corridor.permit_id,
        waypoints=[{"lat": w[0], "lon": w[1]} for w in json.loads(corridor.waypoints)],
        buffer_m=corridor.buffer_m,
    )


@router.put("/permits/{permit_id}/corridor", response_model=PSVCorridorResponse)
def set_permit_corridor(
    permit_id: str,
    payload: PSVCorridorSet,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    permit = db.query(PSVPermit).filter(PSVPermit.id == permit_id).first()
    if not permit:
        raise HTTPException(status_code=404, detail="Permit not found")
    waypoints = json.dumps([[w.lat, w.lon] for w in payload.waypoints])
    corridor = db.query(PSVCorridor).filter(PSVCorridor.permit_id == permit.id).first()
    if corridor:
        corridor.waypoints, corridor.buffer_m, corridor.updated_by = waypoints, payload.buffer_m, current_user.id
    else:
        corridor = PSVCorridor(
            permit_id=permit.id, waypoints=waypoints, buffer_m=payload.buffer_m, updated_by=current_user.id
        )
        db.add(corridor)
    log_action(
        db, "set_corridor", "psv_permit", str(permit.id),
        f"Route corridor for {permit.permit_number}: {len(payload.waypoints)} points, {payload.buffer_m} m", current_user.id,
    )
    db.commit()
    db.refresh(corridor)
    return _corridor_response(corridor)


@router.get("/permits/{permit_id}/corridor", response_model=PSVCorridorResponse)
def get_permit_corridor(
    permit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    corridor = db.query(PSVCorridor).filter(PSVCorridor.permit_id == permit_id).first()
    if not corridor:
        raise HTTPException(status_code=404, detail="No route corridor set for this permit")
    return _corridor_response(corridor)


@router.post("/positions", response_model=PSVPositionResult)
def report_position(
    payload: PSVPositionReport,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    """Check a PSV's GPS position against its permit corridor; raise an alert if it has strayed."""
    plate = payload.plate_number.upper()
    vehicle = db.query(Vehicle).filter(Vehicle.registration_number == plate).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail=f"No vehicle registered with plate {plate}")
    permit = (
        db.query(PSVPermit)
        .filter(
            PSVPermit.vehicle_id == vehicle.id,
            PSVPermit.status == PSVPermitStatus.ACTIVE,
            PSVPermit.expiry_date >= datetime.utcnow(),
        )
        .first()
    )
    if not permit:
        raise HTTPException(status_code=404, detail="No active PSV permit for this vehicle")
    corridor = db.query(PSVCorridor).filter(PSVCorridor.permit_id == permit.id).first()
    if not corridor:
        raise HTTPException(status_code=409, detail="This permit has no route corridor set yet")

    distance = round(distance_to_corridor_m(payload.latitude, payload.longitude, json.loads(corridor.waypoints)))
    inside = distance <= corridor.buffer_m
    result = PSVPositionResult(
        plate_number=plate, permit_number=permit.permit_number, inside=inside,
        distance_m=distance, buffer_m=corridor.buffer_m,
    )
    if inside:
        return result

    now = datetime.now(timezone.utc)
    recent = (
        db.query(PSVRouteAlert)
        .filter(
            PSVRouteAlert.permit_id == permit.id,
            PSVRouteAlert.acknowledged == False,  # noqa: E712
            PSVRouteAlert.created_at >= (now - timedelta(minutes=ALERT_DEDUPE_MINUTES)).replace(tzinfo=None),
        )
        .first()
    )
    if recent:
        result.alert_id, result.alert_deduplicated = recent.id, True
        return result

    alert = PSVRouteAlert(
        permit_id=permit.id, vehicle_id=vehicle.id,
        latitude=payload.latitude, longitude=payload.longitude,
        distance_m=distance, buffer_m=corridor.buffer_m,
        occurred_at=payload.timestamp or now,
    )
    db.add(alert)
    db.flush()
    log_action(
        db, "route_violation", "psv_permit", str(permit.id),
        f"{plate} is {distance} m off route {permit.route} (limit {corridor.buffer_m} m)", current_user.id,
    )
    broadcast(
        db, "psv_route_violation",
        {"registration": plate, "route": permit.route, "distance": distance, "limit": corridor.buffer_m},
        roles=["officer", "admin"],
    )
    db.commit()
    result.alert_id = alert.id
    return result


@router.get("/alerts", response_model=list[PSVRouteAlertResponse])
def list_route_alerts(
    acknowledged: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    query = db.query(PSVRouteAlert)
    if acknowledged is not None:
        query = query.filter(PSVRouteAlert.acknowledged == acknowledged)
    return query.order_by(PSVRouteAlert.occurred_at.desc()).limit(200).all()


@router.post("/alerts/{alert_id}/acknowledge", response_model=PSVRouteAlertResponse)
def acknowledge_route_alert(
    alert_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    alert = db.query(PSVRouteAlert).filter(PSVRouteAlert.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged, alert.acknowledged_by = True, current_user.id
    log_action(db, "ack_route_alert", "psv_route_alert", str(alert.id), "Acknowledged", current_user.id)
    db.commit()
    db.refresh(alert)
    return alert
