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
from app.models.anpr import ANPREvent
from app.models.driver_report import DriverReport
from app.models.enforcement import Appeal, Challan, ChallanStatus, Violation
from app.models.inspection import FitnessCertificate
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
    dials = [
        {"id": "collection", "zones": ZONES_HIGH, "label": "Fines collected", "unit": "%", "value": collection, "max": 100, "zone": _zone(collection, True), "note": "of the value issued"},
        {"id": "insured", "zones": ZONES_HIGH, "label": "Vehicles insured", "unit": "%", "value": insured_pct, "max": 100, "zone": _zone(insured_pct, True), "note": "with an active policy"},
        {"id": "fit", "zones": ZONES_HIGH, "label": "Fitness certified", "unit": "%", "value": fit_pct, "max": 100, "zone": _zone(fit_pct, True), "note": "with a valid certificate"},
        {"id": "overdue", "zones": ZONES_LOW, "label": "Fines overdue", "unit": "%", "value": overdue_share, "max": 100, "zone": _zone(overdue_share, False), "note": "of fines still owed"},
        {"id": "pace", "zones": ZONES_PACE, "label": "Offences this month", "unit": "% of avg", "value": min(pace, 200), "max": 200, "zone": "green" if pace <= 100 else "amber" if pace <= 130 else "red", "note": f"{this_month} so far vs {avg_month:.0f} a month"},
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
