"""Business intelligence: where, when and why offences happen, and the health of the system as dials.

Everything is computed from the live records. Region comes from the offence's GPS position (or its
place name), season from the Zambian calendar (rainy Nov to Apr, cold dry May to Aug, hot dry Sep to Oct).
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission
from app.core.security import OFFICERS, require_role
from app.services import fines as fines_service
from app.models.anpr import ANPREvent
from app.models.driver_report import DriverReport
from app.models.enforcement import Appeal, Challan, ChallanStatus, Violation
from app.models.inspection import FitnessCertificate, Inspection, InspectionResult
from app.models.insurance import Insurance
from app.models.psv import PSVRouteAlert
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleStatus
from app.services.geography import SEASONS, province_of, road_of, season_of

router = APIRouter(prefix="/api/bi", tags=["BI"])
ROW_CAP = 200_000


ZONES_HIGH = [[0, 50, "red"], [50, 75, "amber"], [75, 100, "green"]]
ZONES_LOW = [[0, 15, "green"], [15, 30, "amber"], [30, 100, "red"]]
ZONES_PACE = [[0, 100, "green"], [100, 130, "amber"], [130, 200, "red"]]


def _zone(value: float, higher_is_better: bool) -> str:
    good = value >= 75 if higher_is_better else value <= 15
    ok = value >= 50 if higher_is_better else value <= 30
    return "green" if good else "amber" if ok else "red"


@router.get("/")
def bi(days: int = Query(365, ge=30, le=1825), db: Session = Depends(get_db),
       current_user: User = Depends(require_role(*OFFICERS)), _perm: User = Depends(require_permission("reports:view"))):
    now = datetime.utcnow()
    since = now - timedelta(days=days)

    rows = (db.query(Violation.location, Violation.latitude, Violation.longitude, Violation.timestamp, Violation.violation_type,
                     func.coalesce(Challan.penalty_amount, 0))
            .outerjoin(Challan, Challan.violation_id == Violation.id)
            .filter(Violation.timestamp >= since).limit(ROW_CAP).all())
    road, region, season, hour, weekday, kind = Counter(), Counter(), Counter(), Counter(), Counter(), Counter()
    road_value = defaultdict(int)
    grid = defaultdict(Counter)  # road -> season -> count
    months_seen = defaultdict(set)
    for loc, lat, lon, ts, vt, amount in rows:
        r = road_of(loc)
        road[r] += 1
        road_value[r] += int(amount or 0)
        region[province_of(lat, lon, loc)] += 1
        s = season_of(ts.month)
        season[s] += 1
        grid[r][s] += 1
        months_seen[s].add((ts.year, ts.month))
        hour[ts.hour] += 1
        weekday[ts.weekday()] += 1
        kind[vt.value if hasattr(vt, "value") else str(vt)] += 1
    total = len(rows)

    def top(counter, n=8, extra=None):
        return [{"name": k, "count": v, **({"value": extra[k]} if extra else {})} for k, v in counter.most_common(n)]

    seasons = []
    for name in SEASONS:
        n_months = max(len(months_seen[name]), 1)
        seasons.append({"name": name, "count": season[name], "months": len(months_seen[name]), "per_month": round(season[name] / n_months, 1)})
    busiest = max(seasons, key=lambda x: x["per_month"]) if total else None

    top_roads = [r for r, _ in road.most_common(6)]
    heat = {"seasons": list(SEASONS), "rows": [{"road": r, "cells": [grid[r][s] for s in SEASONS]} for r in top_roads]}
    day_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    bands = [("Night 00-05", range(0, 6)), ("Morning 06-11", range(6, 12)), ("Afternoon 12-17", range(12, 18)), ("Evening 18-23", range(18, 24))]

    fines_service.ensure_current(db)
    # --- dials ---
    issued = db.query(func.coalesce(func.sum(Challan.penalty_amount), 0)).filter(Challan.status != ChallanStatus.CANCELLED, Challan.created_at >= since).scalar() or 0
    paid = db.query(func.coalesce(func.sum(Challan.penalty_amount), 0)).filter(Challan.status == ChallanStatus.PAID, Challan.created_at >= since).scalar() or 0
    collection = round(paid / issued * 100, 1) if issued else 0.0
    open_n = db.query(func.count()).select_from(Challan).filter(Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE])).scalar() or 0
    overdue_n = db.query(func.count()).select_from(Challan).filter(Challan.status == ChallanStatus.OVERDUE).scalar() or 0
    overdue_share = round(overdue_n / open_n * 100, 1) if open_n else 0.0
    vehicles = db.query(func.count()).select_from(Vehicle).filter(Vehicle.status == VehicleStatus.ACTIVE).scalar() or 0
    insured = db.query(func.count(func.distinct(Insurance.vehicle_id))).filter(Insurance.is_active == True, Insurance.end_date >= now).scalar() or 0  # noqa: E712
    fit = db.query(func.count(func.distinct(FitnessCertificate.vehicle_id))).filter(FitnessCertificate.expiry_date >= now).scalar() or 0
    insured_pct = round(min(insured / vehicles * 100, 100), 1) if vehicles else 0.0
    fit_pct = round(min(fit / vehicles * 100, 100), 1) if vehicles else 0.0
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    this_month = db.query(func.count()).select_from(Violation).filter(Violation.timestamp >= month_start).scalar() or 0
    avg_month = total / max(days / 30.4, 1)
    pace = round(this_month / avg_month * 100, 1) if avg_month else 0.0
    def zmw(n):
        return f"ZMW {int(n) // 100:,}"

    dials = [
        {"id": "collection", "zones": ZONES_HIGH, "label": "Fines collected", "unit": "%", "value": collection, "max": 100, "zone": _zone(collection, True),
         "note": f"{zmw(paid)} paid of {zmw(issued)} issued",
         "measures": "How much of the money fined has actually been paid.",
         "out_of": f"{zmw(paid)} paid out of {zmw(issued)} issued, on fines created in the last {days} days (cancelled fines left out).",
         "why": "Unpaid fines mean no deterrent and lost revenue. This is the headline revenue-leakage figure.", "good": "High. 75% or more is green."},
        {"id": "insured", "zones": ZONES_HIGH, "label": "Vehicles insured", "unit": "%", "value": insured_pct, "max": 100, "zone": _zone(insured_pct, True),
         "note": f"{insured} of {vehicles} active vehicles",
         "measures": "How many registered vehicles have a valid insurance policy today.",
         "out_of": f"{insured} insured out of {vehicles} active registered vehicles.",
         "why": "Uninsured vehicles leave accident victims unpaid. It shows where insurance enforcement is needed.", "good": "High. 75% or more is green."},
        {"id": "fit", "zones": ZONES_HIGH, "label": "Fitness certified", "unit": "%", "value": fit_pct, "max": 100, "zone": _zone(fit_pct, True),
         "note": f"{fit} of {vehicles} active vehicles",
         "measures": "How many registered vehicles hold an unexpired fitness (roadworthiness) certificate.",
         "out_of": f"{fit} with a valid certificate out of {vehicles} active registered vehicles.",
         "why": "Unfit vehicles are a direct road-safety risk, so a low figure points to missed inspections.", "good": "High. 75% or more is green."},
        {"id": "overdue", "zones": ZONES_LOW, "label": "Fines overdue", "unit": "%", "value": overdue_share, "max": 100, "zone": _zone(overdue_share, False),
         "note": f"{overdue_n} of {open_n} fines still owed",
         "measures": "Of the fines still owed, how many are past their due date.",
         "out_of": f"{overdue_n} overdue out of {open_n} unpaid or overdue fines (all time).",
         "why": "Shows how quickly people pay. A rising share means reminders and follow-up are not working.", "good": "Low. 15% or less is green."},
        {"id": "pace", "zones": ZONES_PACE, "label": "Offences this month", "unit": "% of avg", "value": min(pace, 200), "max": 200, "zone": "green" if pace <= 100 else "amber" if pace <= 130 else "red",
         "note": f"{this_month} so far vs {avg_month:.0f} a month",
         "measures": "Offences recorded this calendar month compared with the usual monthly number.",
         "out_of": f"{this_month} offences this month against an average of {avg_month:.0f} a month over the last {days} days.",
         "why": "An early signal of a spike, so patrols can be moved before the month ends.", "good": "At or under 100% of the average is green."},
    ]

    # --- warning lights ---
    since24 = now - timedelta(hours=24)
    expiring = (db.query(func.count()).select_from(Insurance).filter(Insurance.is_active == True, Insurance.end_date >= now, Insurance.end_date < now + timedelta(days=30)).scalar() or 0) + (  # noqa: E712
        db.query(func.count()).select_from(FitnessCertificate).filter(FitnessCertificate.expiry_date >= now, FitnessCertificate.expiry_date < now + timedelta(days=30)).scalar() or 0)
    stolen_seen = (db.query(func.count()).select_from(ANPREvent).join(Vehicle, Vehicle.id == ANPREvent.vehicle_id)
                   .filter(Vehicle.status == VehicleStatus.STOLEN, ANPREvent.timestamp >= since24).scalar() or 0)
    lights = [
        {"id": "overdue", "label": "Overdue fines", "count": overdue_n, "level": "red", "view": "challans"},
        {"id": "expiring", "label": "Expiring in 30 days", "count": expiring, "level": "amber", "view": "insights"},
        {"id": "stolen", "label": "Stolen car seen (24 h)", "count": stolen_seen, "level": "red", "view": "anpr"},
        {"id": "psv", "label": "PSV off route", "count": db.query(func.count()).select_from(PSVRouteAlert).filter(PSVRouteAlert.acknowledged == False).scalar() or 0, "level": "amber", "view": "psv"},  # noqa: E712
        {"id": "reports", "label": "Driver reports to review", "count": db.query(func.count()).select_from(DriverReport).filter(DriverReport.status == "pending").scalar() or 0, "level": "amber", "view": "driver_reports"},
        {"id": "appeals", "label": "Appeals to decide", "count": db.query(func.count()).select_from(Appeal).filter(Appeal.status == "pending").scalar() or 0, "level": "amber", "view": "challans"},
    ]
    for l in lights:
        l["on"] = l["count"] > 0

    return {
        "window_days": days, "offences": total, "capped": total >= ROW_CAP,
        "dials": dials, "lights": lights,
        "roads": top(road, 8, road_value), "regions": top(region, 10), "seasons": seasons,
        "busiest_season": busiest["name"] if busiest else None, "heat": heat,
        "weekdays": [{"name": day_names[i], "count": weekday[i]} for i in range(7)],
        "day_parts": [{"name": n, "count": sum(hour[h] for h in r)} for n, r in bands],
        "types": top(kind, 8),
    }


@router.get("/inspections")
def inspection_analytics(days: int = Query(365, ge=30, le=1825), db: Session = Depends(get_db),
                         current_user: User = Depends(require_role(*OFFICERS)), _perm: User = Depends(require_permission("reports:view"))):
    """Why vehicles fail: top defects, failure rate by make and by centre, and the monthly trend."""
    import json
    from app.services import inspection_rules

    since = datetime.utcnow() - timedelta(days=days)
    rows = (db.query(Inspection, Vehicle.make).join(Vehicle, Vehicle.id == Inspection.vehicle_id)
            .filter(Inspection.created_at >= since, Inspection.result.in_([InspectionResult.PASSED, InspectionResult.FAILED])).limit(ROW_CAP).all())
    defects, by_make, by_centre, by_month = Counter(), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
    for insp, make in rows:
        failed = insp.result == InspectionResult.FAILED
        for bucket, key in ((by_make, make), (by_centre, insp.inspection_centre), (by_month, insp.created_at.strftime("%Y-%m"))):
            bucket[key][0] += 1
            bucket[key][1] += 1 if failed else 0
        if failed:
            try:
                _, reasons = inspection_rules.evaluate(json.loads(insp.checklist or "{}"), json.loads(insp.readings or "{}"),
                                                       json.loads(insp.obd_codes or "[]"))
            except (ValueError, TypeError):
                reasons = []
            for r in reasons:
                key = r.split(" failed")[0]
                for label, *_ in inspection_rules.READINGS.values():  # group a reading by its name, not its value
                    if r.startswith(label) and (" is below" in r or " is above" in r):
                        key = f"{label} outside limit"
                if r.startswith("OBD-II fault"):
                    key = "OBD-II fault: " + r.split(":")[0].split()[-1]
                defects[key] += 1
            if not reasons:
                defects["Other (free-text finding)"] += 1

    def rate(d, min_n=1):
        return sorted(({"name": k, "tested": t, "failed": f, "fail_rate": round(100 * f / t)} for k, (t, f) in d.items() if t >= min_n),
                      key=lambda r: (-r["fail_rate"], -r["tested"]))
    total, failed_n = len(rows), sum(1 for i, _ in rows if i.result == InspectionResult.FAILED)
    return {"tested": total, "failed": failed_n, "fail_rate": round(100 * failed_n / total) if total else 0,
            "top_defects": [{"defect": k, "count": n} for k, n in defects.most_common(8)],
            "by_make": rate(by_make)[:8], "by_centre": rate(by_centre)[:8],
            "by_month": [{"month": k, "tested": t, "failed": f} for k, (t, f) in sorted(by_month.items())]}
