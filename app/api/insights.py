"""Decision support: where to look first.

Everything here is derived from the records the other screens keep. The scores
are simple, visible rules (no hidden model), so an officer can see why a vehicle
is near the top.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import OFFICERS, require_role
from app.models.accident import Accident, AccidentSeverity
from app.models.driver import Driver, DriverStatus
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.inspection import FitnessCertificate
from app.models.insurance import Insurance
from app.models.psv import PSVPermit, PSVPermitStatus
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services import settings as runtime_settings

router = APIRouter(prefix="/api/insights", tags=["Insights"])

SEVERITY_WEIGHT = {AccidentSeverity.FATAL: 10, AccidentSeverity.SERIOUS: 4, AccidentSeverity.MINOR: 1}
RISK_RULES = "Each offence in 180 days +2, each unpaid fine +3, each overdue fine +5, no valid insurance +4, no valid fitness certificate +3, blacklisted +6."


def _bands(db: Session, column, extra=(), days=(30, 60, 90)):
    now = datetime.utcnow()
    out = {}
    for d in days:
        q = db.query(func.count()).filter(column >= now, column < now + timedelta(days=d), *extra)
        out[str(d)] = q.scalar() or 0
    return out


@router.get("/")
def insights(db: Session = Depends(get_db), current_user: User = Depends(require_role(*OFFICERS))):
    now = datetime.utcnow()
    since = now - timedelta(days=180)

    offences = dict(
        db.query(Violation.vehicle_id, func.count()).filter(Violation.vehicle_id.isnot(None), Violation.created_at >= since)
        .group_by(Violation.vehicle_id).all()
    )
    open_fines = {}
    for vid, st, n in (
        db.query(Challan.vehicle_id, Challan.status, func.count())
        .filter(Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE])).group_by(Challan.vehicle_id, Challan.status).all()
    ):
        open_fines.setdefault(vid, {})[st] = n
    candidates = set(offences) | set(open_fines)
    insured = {v for (v,) in db.query(Insurance.vehicle_id).filter(Insurance.is_active == True, Insurance.end_date >= now)}  # noqa: E712
    fit = {v for (v,) in db.query(FitnessCertificate.vehicle_id).filter(FitnessCertificate.expiry_date >= now)}
    risk = []
    for v in db.query(Vehicle).filter(Vehicle.id.in_(candidates)).all() if candidates else []:
        parts = []
        n = offences.get(v.id, 0)
        unpaid = open_fines.get(v.id, {}).get(ChallanStatus.UNPAID, 0)
        overdue = open_fines.get(v.id, {}).get(ChallanStatus.OVERDUE, 0)
        score = n * 2 + unpaid * 3 + overdue * 5
        if n: parts.append(f"{n} offence(s)")
        if unpaid: parts.append(f"{unpaid} unpaid")
        if overdue: parts.append(f"{overdue} overdue")
        if v.id not in insured: score += 4; parts.append("no insurance")
        if v.id not in fit: score += 3; parts.append("no fitness")
        if v.is_blacklisted: score += 6; parts.append("blacklisted")
        risk.append({"plate": v.registration_number, "vehicle": f"{v.make} {v.model}", "score": score, "why": ", ".join(parts)})
    risk.sort(key=lambda r: -r["score"])

    weight = case(
        *[(Accident.severity == k, w) for k, w in SEVERITY_WEIGHT.items()], else_=1
    )
    spots = (
        db.query(Accident.location, func.count(), func.sum(weight))
        .filter(Accident.occurred_at >= now - timedelta(days=365)).group_by(Accident.location)
        .order_by(func.sum(weight).desc()).limit(6).all()
    )
    violation_spots = (
        db.query(Violation.location, func.count()).group_by(Violation.location)
        .order_by(func.count().desc()).limit(6).all()
    )

    fit_fee = runtime_settings.get(db, "fees.fitness_test")
    fit_due = _bands(db, FitnessCertificate.expiry_date)
    pipeline = {
        "Insurance": _bands(db, Insurance.end_date, (Insurance.is_active == True,)),  # noqa: E712
        "Fitness certificates": fit_due,
        "Driving licences": _bands(db, Driver.licence_expiry_date, (Driver.status == DriverStatus.ACTIVE,)),
        "PSV permits": _bands(db, PSVPermit.expiry_date, (PSVPermit.status == PSVPermitStatus.ACTIVE,)),
    }
    return {
        "risk_rules": RISK_RULES,
        "risky_vehicles": risk[:8],
        "accident_hotspots": [{"location": l, "accidents": n, "weight": int(w or 0)} for l, n, w in spots],
        "offence_hotspots": [{"location": l, "offences": n} for l, n in violation_spots],
        "expiry_pipeline": pipeline,
        "fitness_fees_expected_90d": fit_due["90"] * fit_fee,
    }
