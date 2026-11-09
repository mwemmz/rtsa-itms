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
from fastapi import HTTPException

from app.services import fines as fines_service
from app.models.accident import Accident, AccidentSeverity, AccidentVehicle
from app.models.inspection import Inspection, InspectionResult
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
    fines_service.ensure_current(db)
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


# ---------------------------------------------------------------------------------------------
# Vehicle passport: one call that answers "can this vehicle be on the road, and how closely should we watch it?"

def _risk_band(score: int) -> str:
    return "high" if score >= 12 else "medium" if score >= 5 else "low"


INTERVAL_DAYS = {"low": 365, "medium": 180, "high": 90}


def vehicle_passport(db: Session, v: Vehicle) -> dict:
    fines_service.ensure_current(db)
    now = datetime.utcnow()
    cert = (db.query(FitnessCertificate).filter(FitnessCertificate.vehicle_id == v.id)
            .order_by(FitnessCertificate.expiry_date.desc()).first())
    policy = (db.query(Insurance).filter(Insurance.vehicle_id == v.id, Insurance.is_active == True)  # noqa: E712
              .order_by(Insurance.end_date.desc()).first())
    reg_expiry = (v.registration_expiry or (v.registration_date + timedelta(days=365))).replace(tzinfo=None)
    unpaid = db.query(func.count()).filter(Challan.vehicle_id == v.id, Challan.status == ChallanStatus.UNPAID).scalar() or 0
    overdue = db.query(func.count()).filter(Challan.vehicle_id == v.id, Challan.status == ChallanStatus.OVERDUE).scalar() or 0
    offences = db.query(func.count()).filter(Violation.vehicle_id == v.id, Violation.created_at >= now - timedelta(days=180)).scalar() or 0
    crashes = (db.query(func.count()).select_from(AccidentVehicle).join(Accident, Accident.id == AccidentVehicle.accident_id)
               .filter(AccidentVehicle.vehicle_id == v.id, Accident.occurred_at >= now - timedelta(days=730)).scalar() or 0)
    insp = db.query(Inspection).filter(Inspection.vehicle_id == v.id).all()
    failed = sum(1 for i in insp if i.result == InspectionResult.FAILED)
    age = max(0, now.year - v.year)

    checks = [
        {"name": "Registration", "ok": reg_expiry >= now, "detail": f"paid until {reg_expiry:%d %b %Y}"},
        {"name": "Fitness certificate", "ok": bool(cert and cert.expiry_date >= now),
         "detail": f"valid until {cert.expiry_date:%d %b %Y}" if cert and cert.expiry_date >= now else "none valid"},
        {"name": "Insurance", "ok": bool(policy and policy.end_date >= now),
         "detail": f"{policy.policy_type.replace('_', ' ')} until {policy.end_date:%d %b %Y}" if policy and policy.end_date >= now else "no active cover"},
        {"name": "Fines", "ok": unpaid + overdue == 0, "detail": f"{unpaid} unpaid, {overdue} overdue"},
        {"name": "Status", "ok": not v.is_blacklisted and v.status.value != "stolen",
         "detail": "blacklisted" if v.is_blacklisted else v.status.value},
    ]
    factors = []
    def add(points, why):
        if points: factors.append({"points": points, "why": why})
    add(overdue * 5, f"{overdue} overdue fine(s)")
    add(unpaid * 3, f"{unpaid} unpaid fine(s)")
    add(min(offences, 5) * 2, f"{offences} offence(s) in 180 days")
    add(min(crashes, 3) * 4, f"{crashes} crash(es) in 2 years")
    add(min(failed, 3) * 2, f"{failed} failed inspection(s)")
    add(4 if not checks[2]["ok"] else 0, "no valid insurance")
    add(3 if not checks[1]["ok"] else 0, "no valid fitness certificate")
    add(2 if age >= 15 else 1 if age >= 10 else 0, f"vehicle is {age} years old")
    add(6 if v.is_blacklisted else 0, "blacklisted")
    score = sum(f["points"] for f in factors)
    band = _risk_band(score)

    actions = []
    if not checks[1]["ok"]: actions.append("Book a fitness test")
    if not checks[2]["ok"]: actions.append("Owner must renew insurance before the vehicle is on the road")
    if overdue or unpaid: actions.append("Clear outstanding fines (a toll gate will stop this vehicle)")
    if not checks[0]["ok"]: actions.append("Renew registration")
    if band == "high": actions.append("Roadside check and a short-interval inspection (90 days)")
    last_pass = max((i.created_at for i in insp if i.result == InspectionResult.PASSED), default=None)
    interval = INTERVAL_DAYS[band]
    next_due = (last_pass.replace(tzinfo=None) + timedelta(days=interval)) if last_pass else now
    return {
        "plate": v.registration_number, "vehicle": f"{v.make} {v.model} {v.year}", "owner": v.owner_name,
        "checks": checks, "can_operate": all(c["ok"] for c in checks), "risk_score": score, "risk_band": band, "factors": factors,
        "recommended_interval_days": interval, "recommended_next_inspection": next_due.isoformat(),
        "interval_rule": "Low risk: yearly. Medium (score 5 to 11): every 6 months. High (12 or more): every 3 months.",
        "next_actions": actions or ["No action needed"],
    }


@router.get("/vehicle/{vehicle_id}")
def passport(vehicle_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(*OFFICERS))):
    v = db.get(Vehicle, vehicle_id)
    if v is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle_passport(db, v)


@router.get("/driver/{driver_id}")
def driver_profile(driver_id: str, db: Session = Depends(get_db), current_user: User = Depends(require_role(*OFFICERS))):
    from app.services.demerits import active_points
    d = db.get(Driver, driver_id)
    if d is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    now = datetime.utcnow()
    limit = runtime_settings.get(db, "enforcement.demerit_limit")
    pts = active_points(db, d.id, now)
    recent = db.query(func.count()).filter(Violation.driver_id == d.id, Violation.created_at >= now - timedelta(days=90)).scalar() or 0
    before = db.query(func.count()).filter(Violation.driver_id == d.id, Violation.created_at >= now - timedelta(days=180),
                                           Violation.created_at < now - timedelta(days=90)).scalar() or 0
    kinds = (db.query(Violation.violation_type, func.count()).filter(Violation.driver_id == d.id, Violation.created_at >= now - timedelta(days=365))
             .group_by(Violation.violation_type).all())
    trend = "worsening" if recent > before else "improving" if recent < before else "steady"
    left = max(0, limit - pts)
    band = "high" if left <= 3 or d.status == DriverStatus.SUSPENDED else "medium" if pts >= limit // 2 else "low"
    advice = []
    if d.status == DriverStatus.SUSPENDED: advice.append("Licence suspended: the driver may not drive")
    elif left <= 3: advice.append(f"Only {left} point(s) from automatic suspension")
    if d.licence_expiry_date < now: advice.append("Licence has expired")
    elif d.licence_expiry_date < now + timedelta(days=60): advice.append(f"Licence expires {d.licence_expiry_date:%d %b %Y}: remind the driver")
    if trend == "worsening": advice.append("Offences are rising: consider a driver-awareness course")
    return {"name": f"{d.first_name} {d.last_name}", "licence": d.licence_number, "status": d.status.value,
            "points": pts, "limit": limit, "points_left": left, "risk_band": band, "trend": trend,
            "offences_90d": recent, "offences_prior_90d": before,
            "by_type": [{"type": t.value, "count": n} for t, n in kinds], "advice": advice or ["No action needed"]}


@router.get("/insurance-gaps")
def insurance_gaps(db: Session = Depends(get_db), current_user: User = Depends(require_role(*OFFICERS))):
    """Who is on the road without cover, who is about to lose it, and where uninsured cars cause trouble."""
    now = datetime.utcnow()
    covered = {v for (v,) in db.query(Insurance.vehicle_id).filter(Insurance.is_active == True, Insurance.end_date >= now)}  # noqa: E712
    active = [v for v in db.query(Vehicle).all() if v.status.value == "active"]
    uninsured = [v for v in active if v.id not in covered]
    expiring = (db.query(Insurance, Vehicle).join(Vehicle, Vehicle.id == Insurance.vehicle_id)
                .filter(Insurance.is_active == True, Insurance.end_date >= now, Insurance.end_date < now + timedelta(days=30))  # noqa: E712
                .order_by(Insurance.end_date).limit(10).all())
    offending = {v for (v,) in db.query(Violation.vehicle_id).filter(Violation.vehicle_id.isnot(None),
                 Violation.created_at >= now - timedelta(days=180))}
    crashed = {v for (v,) in db.query(AccidentVehicle.vehicle_id).filter(AccidentVehicle.vehicle_id.isnot(None))}
    unins_ids = {v.id for v in uninsured}
    total = len(active) or 1
    return {
        "fleet": len(active), "uninsured": len(uninsured), "uninsured_pct": round(100 * len(uninsured) / total, 1),
        "uninsured_with_offences": len(unins_ids & offending), "uninsured_in_crashes": len(unins_ids & crashed),
        "worklist": [{"plate": v.registration_number, "owner": v.owner_name, "vehicle": f"{v.make} {v.model}",
                      "priority": "high" if v.id in offending or v.id in crashed else "normal"}
                     for v in sorted(uninsured, key=lambda x: (x.id not in offending and x.id not in crashed, x.registration_number))[:12]],
        "expiring_30d": [{"plate": v.registration_number, "owner": v.owner_name, "ends": i.end_date.isoformat(), "type": i.policy_type}
                         for i, v in expiring],
        "role": "Insurance proves a vehicle can pay for the harm it causes. It is checked on the passport, required before a PSV permit renews, and used to rank enforcement.",
    }


@router.get("/accident-factors")
def accident_factors(db: Session = Depends(get_db), current_user: User = Depends(require_role(*OFFICERS))):
    """Compare vehicles that were in crashes with the whole fleet: what do the crashed ones have in common?"""
    now = datetime.utcnow()
    crashed_ids = {v for (v,) in db.query(AccidentVehicle.vehicle_id).join(Accident, Accident.id == AccidentVehicle.accident_id)
                   .filter(AccidentVehicle.vehicle_id.isnot(None), Accident.occurred_at >= now - timedelta(days=730))}
    fleet = db.query(Vehicle).all()
    insured = {v for (v,) in db.query(Insurance.vehicle_id).filter(Insurance.is_active == True, Insurance.end_date >= now)}  # noqa: E712
    fit = {v for (v,) in db.query(FitnessCertificate.vehicle_id).filter(FitnessCertificate.expiry_date >= now)}
    offend = {v for (v,) in db.query(Violation.vehicle_id).filter(Violation.vehicle_id.isnot(None), Violation.created_at >= now - timedelta(days=365))}
    tests = {
        "No valid insurance": lambda v: v.id not in insured,
        "No valid fitness certificate": lambda v: v.id not in fit,
        "10 years old or more": lambda v: now.year - v.year >= 10,
        "Had an offence in the last year": lambda v: v.id in offend,
    }
    crashed = [v for v in fleet if v.id in crashed_ids]
    out = []
    for name, f in tests.items():
        a = 100 * sum(1 for v in crashed if f(v)) / len(crashed) if crashed else 0
        b = 100 * sum(1 for v in fleet if f(v)) / len(fleet) if fleet else 0
        out.append({"factor": name, "crashed_pct": round(a), "fleet_pct": round(b), "lift": round(a / b, 1) if b else None})
    out.sort(key=lambda r: -(r["lift"] or 0))
    return {"crashed_vehicles": len(crashed), "fleet": len(fleet), "factors": out,
            "reading": "A lift above 1.0 means crashed vehicles have this more often than the fleet as a whole. It shows association, not cause."}
