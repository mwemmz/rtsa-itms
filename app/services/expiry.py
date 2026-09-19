"""Expiry reminders: licences, insurance, fitness certificates, PSV permits.

Reminders fire at the thresholds configured in ``notifications.expiry_reminder_days``
(default 30/14/7/1 days). Each (entity, expiry date, threshold) triple is notified
once thanks to the notification ``dedupe_key`` — safe to run as often as you like.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.inspection import FitnessCertificate
from app.models.insurance import Insurance
from app.models.psv import PSVPermit, PSVPermitStatus
from app.models.vehicle import Vehicle
from app.services import settings as runtime_settings
from app.services.notifications import notify


def _bucket(days_left: int, thresholds: list[int]) -> int | None:
    """Smallest configured threshold that still covers ``days_left``."""
    covering = [t for t in thresholds if days_left <= t]
    return min(covering) if covering else None


def _fire(db: Session, user_id, event: str, expiry: datetime, ref: str,
          variables: dict, thresholds: list[int], now: datetime) -> int:
    days_left = (expiry.date() - now.date()).days
    if days_left < 0:
        return 0
    bucket = _bucket(days_left, thresholds)
    if bucket is None:
        return 0
    variables = {**variables, "expiry_date": expiry.strftime("%Y-%m-%d"), "days_left": days_left}
    return len(notify(db, user_id, event, variables, dedupe_key=f"{ref}:{expiry:%Y%m%d}:{bucket}"))


def scan_expiries(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    thresholds = runtime_settings.get(db, "notifications.expiry_reminder_days")
    horizon = now + timedelta(days=max(thresholds))
    sent = {"licence": 0, "insurance": 0, "fitness": 0, "psv_permit": 0}

    for d in db.query(Driver).filter(
        Driver.user_id.isnot(None),
        Driver.licence_expiry_date >= now,
        Driver.licence_expiry_date <= horizon,
    ):
        sent["licence"] += _fire(db, d.user_id, "licence_expiring", d.licence_expiry_date,
                                 f"licence:{d.id}", {"licence_number": d.licence_number}, thresholds, now)

    rows = (
        db.query(Insurance, Vehicle)
        .join(Vehicle, Vehicle.id == Insurance.vehicle_id)
        .filter(Vehicle.user_id.isnot(None), Insurance.is_active == True,  # noqa: E712
                Insurance.end_date >= now, Insurance.end_date <= horizon)
    )
    for ins, veh in rows:
        sent["insurance"] += _fire(db, veh.user_id, "insurance_expiring", ins.end_date, f"insurance:{ins.id}",
                                   {"registration": veh.registration_number}, thresholds, now)

    rows = (
        db.query(FitnessCertificate, Vehicle)
        .join(Vehicle, Vehicle.id == FitnessCertificate.vehicle_id)
        .filter(Vehicle.user_id.isnot(None), FitnessCertificate.expiry_date >= now,
                FitnessCertificate.expiry_date <= horizon)
    )
    for cert, veh in rows:
        sent["fitness"] += _fire(db, veh.user_id, "fitness_expiring", cert.expiry_date, f"fitness:{cert.id}",
                                 {"registration": veh.registration_number}, thresholds, now)

    rows = (
        db.query(PSVPermit, Vehicle)
        .join(Vehicle, Vehicle.id == PSVPermit.vehicle_id)
        .filter(Vehicle.user_id.isnot(None), PSVPermit.status == PSVPermitStatus.ACTIVE,
                PSVPermit.expiry_date >= now, PSVPermit.expiry_date <= horizon)
    )
    for permit, veh in rows:
        sent["psv_permit"] += _fire(db, veh.user_id, "psv_permit_expiring", permit.expiry_date, f"psv:{permit.id}",
                                    {"registration": veh.registration_number, "permit_number": permit.permit_number,
                                     "route": permit.route}, thresholds, now)
    if any(sent.values()):
        db.commit()
    return sent
