from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import FIELD_STAFF, OFFICERS, require_role
from app.models.user import User, UserRole
from app.models.driver import Driver, DriverStatus
from app.schemas.driver import DriverCreate, DriverResponse, DriverUpdate
from app.services.audit import log_action

router = APIRouter(prefix="/api/drivers", tags=["Drivers"])


@router.post("/", response_model=DriverResponse, status_code=status.HTTP_201_CREATED)
def create_driver(
    payload: DriverCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    existing = db.query(Driver).filter(
        Driver.licence_number == payload.licence_number
    ).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Driver with this licence number already exists",
        )
    if db.query(Driver.id).filter(Driver.id_number == payload.id_number).first():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A driver with this NRC / ID number already exists")
    driver = Driver(**payload.model_dump())
    db.add(driver)
    db.flush()
    log_action(db, "create", "driver", str(driver.id), f"Created driver licence {driver.licence_number}", current_user.id)
    db.commit()
    db.refresh(driver)
    return driver


@router.get("/", response_model=list[DriverResponse])
def list_drivers(
    search: str | None = Query(None),
    status_filter: DriverStatus | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    query = db.query(Driver)
    if search:
        query = query.filter(
            Driver.licence_number.ilike(f"%{search}%")
            | Driver.first_name.ilike(f"%{search}%")
            | Driver.last_name.ilike(f"%{search}%")
            | Driver.id_number.ilike(f"%{search}%")
        )
    if status_filter:
        query = query.filter(Driver.status == status_filter)
    return query.offset(skip).limit(limit).all()


@router.get("/{driver_id}", response_model=DriverResponse)
def get_driver(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    return driver


@router.get("/by-licence/{licence_number}", response_model=DriverResponse)
def get_driver_by_licence(
    licence_number: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*FIELD_STAFF)),
):
    # Typed off a card at the roadside: ignore case and stray spaces.
    driver = db.query(Driver).filter(func.upper(Driver.licence_number) == licence_number.strip().upper()).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    return driver


@router.patch("/{driver_id}", response_model=DriverResponse)
def update_driver(
    driver_id: str,
    payload: DriverUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(driver, field, value)
    db.flush()
    log_action(db, "update", "driver", str(driver.id), f"Updated driver {driver.licence_number}", current_user.id)
    db.commit()
    db.refresh(driver)
    return driver


@router.delete("/{driver_id}", status_code=status.HTTP_204_NO_CONTENT)
def deactivate_driver(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    driver.status = DriverStatus.SUSPENDED
    db.flush()
    log_action(db, "suspend", "driver", str(driver.id), f"Suspended driver {driver.licence_number}", current_user.id)
    db.commit()
    return None


@router.get("/{driver_id}/record")
def driver_record(
    driver_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """A driver's full record: offences, fines, demerit points, appeals and accidents, with totals."""
    from collections import Counter
    from datetime import datetime, timedelta

    from sqlalchemy import or_

    from app.services import settings as runtime_settings
    from app.models.accident import Accident, AccidentVehicle
    from app.models.enforcement import Appeal, Challan, ChallanStatus, DemeritPoint, Violation
    from app.models.vehicle import Vehicle
    from app.services import fines as fines_service
    from app.services.demerits import active_points

    d = db.get(Driver, driver_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    fines_service.ensure_current(db)
    now = datetime.utcnow()

    violations = (db.query(Violation).filter(Violation.driver_id == d.id)
                  .order_by(Violation.timestamp.desc()).all())
    vids = [v.id for v in violations]
    cond = or_(Challan.driver_id == d.id, Challan.violation_id.in_(vids)) if vids else (Challan.driver_id == d.id)
    challans = db.query(Challan).filter(cond).all()
    by_violation = {c.violation_id: c for c in challans}
    demerits = db.query(DemeritPoint).filter(DemeritPoint.driver_id == d.id).order_by(DemeritPoint.awarded_at.desc()).all()
    pts_by_violation = {p.violation_id: p for p in demerits}
    appeals = db.query(Appeal).filter(Appeal.challan_id.in_([c.id for c in challans])).all() if challans else []
    plates = {}
    if vids:
        for vid, plate in db.query(Violation.id, Vehicle.registration_number).join(Vehicle, Vehicle.id == Violation.vehicle_id).filter(Violation.id.in_(vids)):
            plates[vid] = plate
    acc_rows = (db.query(Accident, AccidentVehicle).join(AccidentVehicle, AccidentVehicle.accident_id == Accident.id)
                .filter(AccidentVehicle.driver_id == d.id).order_by(Accident.occurred_at.desc()).all())

    def when(v):
        return v.timestamp or v.created_at

    def count_since(days):
        cut = now - timedelta(days=days)
        return sum(1 for v in violations if when(v) and when(v).replace(tzinfo=None) >= cut)

    t = fines_service.totals(challans)
    paid = [c for c in challans if c.status == ChallanStatus.PAID]
    types = Counter(v.violation_type.value for v in violations)
    amounts = Counter()
    for v in violations:
        c = by_violation.get(v.id)
        if c: amounts[v.violation_type.value] += c.penalty_amount
    months = []
    for i in range(11, -1, -1):
        y, m = now.year, now.month - i
        while m <= 0: m += 12; y -= 1
        months.append({"month": f"{y}-{m:02d}", "count": 0})
    idx = {x["month"]: x for x in months}
    for v in violations:
        k = when(v).strftime("%Y-%m") if when(v) else None
        if k in idx: idx[k]["count"] += 1
    limit = runtime_settings.get(db, "enforcement.demerit_limit")
    pts = active_points(db, d.id, now)
    sev = Counter(a.severity.value for a, _ in acc_rows)

    owned = []
    if d.user_id:
        owned = [v.registration_number for v in db.query(Vehicle).filter(Vehicle.user_id == d.user_id).all()]

    log_action(db, "view", "driver_record", str(d.id), f"Viewed driver record {d.licence_number}", current_user.id)
    db.commit()
    return {
        "driver": {
            "id": str(d.id), "name": f"{d.first_name} {d.last_name}", "licence_number": d.licence_number,
            "id_number": d.id_number, "licence_class": getattr(d.licence_class, "value", d.licence_class),
            "status": d.status.value, "issue_date": d.licence_issue_date, "expiry_date": d.licence_expiry_date,
            "phone": d.phone_number, "restrictions": d.restrictions, "licence_expired": d.licence_expiry_date < now,
            "vehicles_owned": owned,
        },
        "summary": {
            "offences_total": len(violations), "offences_12m": count_since(365), "offences_90d": count_since(90),
            "fines_total": len(challans), "fines_paid": len(paid), "amount_paid": sum(c.penalty_amount for c in paid),
            "fines_owed": t["owed"], "amount_owed": t["amount_due"], "fines_overdue": t["overdue"],
            "fines_under_appeal": t["under_appeal"],
            "fines_cancelled": sum(1 for c in challans if c.status == ChallanStatus.CANCELLED),
            "amount_issued": sum(c.penalty_amount for c in challans),
            "points": pts, "points_limit": limit, "points_left": max(0, limit - pts),
            "appeals_total": len(appeals), "appeals_upheld": sum(1 for a in appeals if a.status == "upheld"),
            "appeals_dismissed": sum(1 for a in appeals if a.status == "dismissed"),
            "appeals_pending": sum(1 for a in appeals if a.status == "pending"),
            "accidents_total": len(acc_rows), "accidents_by_severity": dict(sev),
            "vehicles_driven": len({plates[v.id] for v in violations if v.id in plates}),
            "first_offence": when(violations[-1]) if violations else None,
            "last_offence": when(violations[0]) if violations else None,
        },
        "by_type": [{"type": k, "count": n, "amount": amounts[k]} for k, n in types.most_common()],
        "by_month": months,
        "offences": [{
            "date": when(v), "type": v.violation_type.value, "location": v.location, "plate": plates.get(v.id),
            "challan": by_violation[v.id].reference if v.id in by_violation else None,
            "amount": by_violation[v.id].penalty_amount if v.id in by_violation else None,
            "fine_status": by_violation[v.id].status.value if v.id in by_violation else None,
            "points": (pts_by_violation[v.id].points if v.id in pts_by_violation and not pts_by_violation[v.id].voided_at else 0),
        } for v in violations],
        "accidents": [{"date": a.occurred_at, "location": a.location, "severity": a.severity.value,
                       "status": a.status.value, "plate": av.plate_number, "role": av.role} for a, av in acc_rows],
        "generated_at": now,
    }
