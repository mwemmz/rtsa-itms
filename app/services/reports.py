"""Reporting & analytics.

Each report is a small definition (columns + a row query) so the same data
feeds the JSON API, CSV, Excel and PDF exports. Aggregations run in SQL so they
stay fast as tables grow.
"""

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.accident import Accident, AccidentVehicle
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.licence import LicenceApplication
from app.models.payment import Payment, PaymentStatus
from app.models.psv import PSVOperator, PSVPermit
from app.models.toll import TollComplianceResult, TollTransaction
from app.models.vehicle import Vehicle
from app.services import payments as payment_service

MAX_EXPORT_ROWS = 50_000


@dataclass
class Report:
    key: str
    title: str
    description: str
    columns: list[str]
    rows: Callable[[Session, datetime, datetime, int], list[list]]
    summary: Callable[[Session, datetime, datetime], dict]


def _v(x):
    """Enum -> value, everything else unchanged."""
    return x.value if hasattr(x, "value") else x


def month_bucket(db: Session, column):
    if db.bind.dialect.name == "sqlite":
        return func.strftime("%Y-%m", column)
    return func.to_char(column, "YYYY-MM")


def _count_by(db: Session, column, *filters) -> dict:
    q = db.query(column, func.count()).group_by(column)
    for f in filters:
        q = q.filter(f)
    return {(_v(k) if k is not None else "unknown"): n for k, n in q.all()}


# --- report definitions ------------------------------------------------------------

def _registrations_rows(db, start, end, limit):
    q = (db.query(Vehicle)
         .filter(Vehicle.registration_date >= start, Vehicle.registration_date <= end)
         .order_by(Vehicle.registration_date.desc()).limit(limit))
    return [[v.registration_number, v.make, v.model, v.year, v.owner_name, v.status.value,
             "yes" if v.is_blacklisted else "no", v.registration_date] for v in q]


def _registrations_summary(db, start, end):
    f = (Vehicle.registration_date >= start, Vehicle.registration_date <= end)
    return {
        "New registrations": db.query(func.count(Vehicle.id)).filter(*f).scalar(),
        "Total vehicles on register": db.query(func.count(Vehicle.id)).scalar(),
        "Blacklisted": db.query(func.count(Vehicle.id)).filter(Vehicle.is_blacklisted == True).scalar(),  # noqa: E712
    }


def _licensing_rows(db, start, end, limit):
    q = (db.query(LicenceApplication)
         .filter(LicenceApplication.created_at >= start, LicenceApplication.created_at <= end)
         .order_by(LicenceApplication.created_at.desc()).limit(limit))
    return [[f"{a.first_name} {a.last_name}", a.requested_class.value, a.status.value,
             a.theory_score if a.theory_score is not None else "", a.practical_score if a.practical_score is not None else "",
             a.issued_licence_number or "", a.created_at] for a in q]


def _licensing_summary(db, start, end):
    f = (LicenceApplication.created_at >= start, LicenceApplication.created_at <= end)
    by_status = _count_by(db, LicenceApplication.status, *f)
    total = sum(by_status.values())
    issued = by_status.get("issued", 0)
    soon = db.query(func.count(Driver.id)).filter(
        Driver.licence_expiry_date >= datetime.utcnow(),
        Driver.licence_expiry_date <= datetime.utcnow() + timedelta(days=30)).scalar()
    return {"Applications": total, "Licences issued": issued,
            "Issue rate": f"{(issued / total * 100):.1f}%" if total else "n/a",
            "Licences expiring in 30 days": soon}


def _violations_rows(db, start, end, limit):
    q = (db.query(Violation, Vehicle.registration_number)
         .outerjoin(Vehicle, Vehicle.id == Violation.vehicle_id)
         .filter(Violation.timestamp >= start, Violation.timestamp <= end)
         .order_by(Violation.timestamp.desc()).limit(limit))
    return [[v.violation_type.value, plate or "", v.location, v.timestamp, v.description or ""] for v, plate in q]


def _violations_summary(db, start, end):
    f = (Violation.timestamp >= start, Violation.timestamp <= end)
    by_type = _count_by(db, Violation.violation_type, *f)
    ch = (Challan.created_at >= start, Challan.created_at <= end)
    billed = db.query(func.coalesce(func.sum(Challan.penalty_amount), 0)).filter(*ch).scalar()
    collected = db.query(func.coalesce(func.sum(Challan.penalty_amount), 0)).filter(
        *ch, Challan.status == ChallanStatus.PAID).scalar()
    out = {"Violations": sum(by_type.values()), "Fines issued": int(billed), "Fines collected": int(collected),
           "Collection rate": f"{(collected / billed * 100):.1f}%" if billed else "n/a"}
    top = sorted(by_type.items(), key=lambda kv: -kv[1])[:3]
    for k, n in top:
        out[f"Top: {k}"] = n
    return out


def _accidents_rows(db, start, end, limit):
    counts = dict(db.query(AccidentVehicle.accident_id, func.count()).group_by(AccidentVehicle.accident_id).all())
    q = (db.query(Accident).filter(Accident.occurred_at >= start, Accident.occurred_at <= end)
         .order_by(Accident.occurred_at.desc()).limit(limit))
    return [[a.location, a.occurred_at, a.severity.value, a.status.value, counts.get(a.id, 0),
             a.description or ""] for a in q]


def _accidents_summary(db, start, end):
    f = (Accident.occurred_at >= start, Accident.occurred_at <= end)
    by_sev = _count_by(db, Accident.severity, *f)
    hot = (db.query(Accident.location, func.count().label("n")).filter(*f)
           .group_by(Accident.location).order_by(func.count().desc()).first())
    out = {"Accidents": sum(by_sev.values())}
    for sev in ("minor", "serious", "fatal"):
        out[f"  {sev.capitalize()}"] = by_sev.get(sev, 0)
    if hot:
        out["Accident hotspot"] = f"{hot[0]} ({hot[1]})"
    return out


def _psv_rows(db, start, end, limit):
    q = (db.query(PSVPermit, PSVOperator.name, Vehicle.registration_number)
         .join(PSVOperator, PSVOperator.id == PSVPermit.operator_id)
         .join(Vehicle, Vehicle.id == PSVPermit.vehicle_id)
         .filter(PSVPermit.issued_date >= start, PSVPermit.issued_date <= end)
         .order_by(PSVPermit.issued_date.desc()).limit(limit))
    return [[p.permit_number, op, plate, p.route, p.issued_date, p.expiry_date, p.status.value] for p, op, plate in q]


def _psv_summary(db, start, end):
    now = datetime.utcnow()
    return {
        "Operators": db.query(func.count(PSVOperator.id)).scalar(),
        "Permits issued in period": db.query(func.count(PSVPermit.id)).filter(
            PSVPermit.issued_date >= start, PSVPermit.issued_date <= end).scalar(),
        "Permits currently valid": db.query(func.count(PSVPermit.id)).filter(PSVPermit.expiry_date >= now).scalar(),
        "Permits expired": db.query(func.count(PSVPermit.id)).filter(PSVPermit.expiry_date < now).scalar(),
    }


def _revenue_rows(db, start, end, limit):
    q = (db.query(Payment).filter(Payment.created_at >= start, Payment.created_at <= end)
         .order_by(Payment.created_at.desc()).limit(limit))
    return [[p.receipt_number or "", p.reference, p.payment_type.value, p.amount, p.refunded_amount,
             p.currency, p.gateway or "", p.status.value, p.paid_at or ""] for p in q]


def _revenue_summary(db, start, end):
    s = payment_service.revenue_summary(db, since=start)
    out = {"Gross revenue": s["gross"], "Refunded": s["refunded"], "Net revenue": s["net"]}
    for k, v in s["by_type"].items():
        out[f"  {k}"] = v["total"]
    return out


def _toll_rows(db, start, end, limit):
    q = (db.query(TollTransaction).filter(TollTransaction.timestamp >= start, TollTransaction.timestamp <= end)
         .order_by(TollTransaction.timestamp.desc()).limit(limit))
    return [[t.plate_number, t.gate_id, t.timestamp, t.compliance_result.value, t.flagged_issues or "",
             t.toll_amount if t.toll_amount is not None else "", "yes" if t.is_paid else "no"] for t in q]


def _toll_summary(db, start, end):
    f = (TollTransaction.timestamp >= start, TollTransaction.timestamp <= end)
    by_result = _count_by(db, TollTransaction.compliance_result, *f)
    total = sum(by_result.values())
    flagged = by_result.get(TollComplianceResult.FLAGGED.value, 0)
    unpaid = db.query(func.coalesce(func.sum(TollTransaction.toll_amount), 0)).filter(
        *f, TollTransaction.is_paid == False).scalar()  # noqa: E712
    return {"Vehicles checked": total, "Flagged": flagged,
            "Non-compliance rate": f"{(flagged / total * 100):.1f}%" if total else "n/a",
            "Unpaid toll value": int(unpaid)}


REPORTS: dict[str, Report] = {r.key: r for r in [
    Report("registrations", "Vehicle Registrations", "New vehicle registrations and the state of the register",
           ["Plate", "Make", "Model", "Year", "Owner", "Status", "Blacklisted", "Registered"],
           _registrations_rows, _registrations_summary),
    Report("licensing", "Driver Licensing", "Licence applications, test results and issuance",
           ["Applicant", "Class", "Status", "Theory score", "Practical score", "Licence number", "Applied"],
           _licensing_rows, _licensing_summary),
    Report("violations", "Traffic Violations", "Violations recorded and fine collection",
           ["Type", "Plate", "Location", "Time", "Description"], _violations_rows, _violations_summary),
    Report("accidents", "Accidents", "Accident reports by severity and location",
           ["Location", "Occurred", "Severity", "Status", "Vehicles", "Description"],
           _accidents_rows, _accidents_summary),
    Report("psv", "PSV Permits", "Public service vehicle operators and route permits",
           ["Permit", "Operator", "Plate", "Route", "Issued", "Expires", "Status"], _psv_rows, _psv_summary),
    Report("revenue", "Revenue", "Payments, refunds and revenue by category",
           ["Receipt", "Reference", "Type", "Amount", "Refunded", "Currency", "Gateway", "Status", "Paid at"],
           _revenue_rows, _revenue_summary),
    Report("toll", "Toll Enforcement", "Toll-gate compliance checks and unpaid tolls",
           ["Plate", "Gate", "Time", "Result", "Issues", "Toll amount", "Paid"], _toll_rows, _toll_summary),
]}


def resolve_range(date_from: datetime | None, date_to: datetime | None, default_days: int) -> tuple[datetime, datetime]:
    end = date_to or datetime.utcnow()
    start = date_from or (end - timedelta(days=default_days))
    if date_to is not None and date_to.hour == 0 and date_to.minute == 0 and date_to.second == 0:
        end = date_to + timedelta(days=1) - timedelta(seconds=1)  # inclusive of the end date
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


# --- consolidated analytics dashboard --------------------------------------------------

_lock = threading.Lock()
_dash_cache: dict[tuple, tuple[float, dict]] = {}


def dashboard(db: Session, start: datetime, end: datetime) -> dict:
    key = (start.date(), end.date())
    now = time.monotonic()
    with _lock:
        hit = _dash_cache.get(key)
        if hit and now - hit[0] < settings.REPORT_CACHE_SECONDS:
            return hit[1]

    def monthly(col, *filters):
        q = db.query(month_bucket(db, col), func.count()).filter(col >= start, col <= end, *filters)
        return {m: n for m, n in q.group_by(month_bucket(db, col)).order_by(month_bucket(db, col)).all()}

    revenue_monthly = {
        m: int(t or 0) for m, t in
        db.query(month_bucket(db, Payment.paid_at), func.sum(Payment.amount - Payment.refunded_amount))
        .filter(Payment.paid_at >= start, Payment.paid_at <= end,
                Payment.status.in_([PaymentStatus.COMPLETED, PaymentStatus.REFUNDED]))
        .group_by(month_bucket(db, Payment.paid_at)).order_by(month_bucket(db, Payment.paid_at)).all()
    }
    data = {
        "range": {"from": start.isoformat(), "to": end.isoformat()},
        "generated_at": datetime.utcnow().isoformat(),
        "kpis": {r.key: r.summary(db, start, end) for r in REPORTS.values()},
        "trends": {
            "registrations": monthly(Vehicle.registration_date),
            "violations": monthly(Violation.timestamp),
            "accidents": monthly(Accident.occurred_at),
            "toll_checks": monthly(TollTransaction.timestamp),
            "revenue": revenue_monthly,
        },
        "breakdowns": {
            "vehicles_by_status": _count_by(db, Vehicle.status),
            "violations_by_type": _count_by(db, Violation.violation_type,
                                            Violation.timestamp >= start, Violation.timestamp <= end),
            "accidents_by_severity": _count_by(db, Accident.severity,
                                               Accident.occurred_at >= start, Accident.occurred_at <= end),
            "challans_by_status": _count_by(db, Challan.status),
            "toll_by_result": _count_by(db, TollTransaction.compliance_result,
                                        TollTransaction.timestamp >= start, TollTransaction.timestamp <= end),
        },
    }
    with _lock:
        _dash_cache[key] = (now, data)
    return data


def clear_cache() -> None:
    with _lock:
        _dash_cache.clear()
