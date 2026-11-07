"""Demerit points: driver offences add points to the driver; too many in the window suspends the licence.

The point values below are illustrative defaults, not RTSA's official schedule. The limit and the
window are admin settings (`enforcement.demerit_limit`, `enforcement.demerit_window_months`).
"""

from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.driver import Driver, DriverStatus
from app.models.enforcement import DemeritPoint, Violation, ViolationType
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify

POINTS = {
    ViolationType.SPEEDING: 3,
    ViolationType.RUNNING_RED_LIGHT: 4,
    ViolationType.USING_PHONE: 3,
    ViolationType.REAR_SEAT_BELT: 2,
    ViolationType.DRUNK_DRIVING: 6,
    ViolationType.RECKLESS_DRIVING: 5,
}


def points_for(violation: Violation) -> int:
    base = POINTS.get(violation.violation_type, 0)
    if violation.violation_type == ViolationType.SPEEDING and violation.speed_kmh and violation.speed_limit_kmh:
        over = violation.speed_kmh - violation.speed_limit_kmh
        base += 2 if over >= 30 else 1 if over >= 15 else 0
    return base


def _window_start(db: Session, now: datetime) -> datetime:
    return now - timedelta(days=30 * runtime_settings.get(db, "enforcement.demerit_window_months"))


def active_points(db: Session, driver_id, now: datetime | None = None) -> int:
    now = now or datetime.utcnow()
    return int(
        db.query(func.coalesce(func.sum(DemeritPoint.points), 0))
        .filter(DemeritPoint.driver_id == driver_id, DemeritPoint.voided_at.is_(None),
                DemeritPoint.awarded_at >= _window_start(db, now))
        .scalar()
    )


def award_points(db: Session, violation: Violation, driver: Driver, actor_id=None) -> int:
    """Add the points for this violation to the driver (once). Returns the points awarded, 0 if none."""
    points = points_for(violation)
    if points <= 0 or db.query(DemeritPoint.id).filter(DemeritPoint.violation_id == violation.id).first():
        return 0
    now = datetime.utcnow()
    db.add(DemeritPoint(
        driver_id=driver.id, violation_id=violation.id, points=points,
        reason=violation.violation_type.value.replace("_", " "), awarded_at=violation.timestamp or now,
    ))
    db.flush()
    total, limit = active_points(db, driver.id, now), runtime_settings.get(db, "enforcement.demerit_limit")
    log_action(db, "demerit_points", "driver", str(driver.id), f"+{points} points ({total} of {limit})", actor_id)
    if driver.user_id:
        notify(db, driver.user_id, "demerit_points_added",
               {"points": points, "offence": violation.violation_type.value.replace("_", " "), "total": total, "limit": limit})
    if total >= limit and driver.status == DriverStatus.ACTIVE:
        driver.status = DriverStatus.SUSPENDED
        log_action(db, "licence_suspended_points", "driver", str(driver.id),
                   f"Licence suspended: {total} demerit points reached the limit of {limit}", actor_id)
        if driver.user_id:
            notify(db, driver.user_id, "licence_suspended_points", {"total": total, "limit": limit})
    return points


def void_points(db: Session, violation_id, reason: str) -> int:
    row = db.query(DemeritPoint).filter(DemeritPoint.violation_id == violation_id, DemeritPoint.voided_at.is_(None)).first()
    if row is None:
        return 0
    row.voided_at, row.voided_reason = datetime.utcnow(), reason[:200]
    return row.points
