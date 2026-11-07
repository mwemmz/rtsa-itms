"""Driver nomination: who was driving when the owner's vehicle was involved in an offence.

When an offence is recorded against a vehicle with no driver on the spot (a toll gate,
a camera, an officer who only had the plate), the owner is asked to name the driver.

    requested -> nominated -> accepted     the named driver takes the fine
                           -> rejected     "that wasn't me": an officer decides
              -> expired                   no answer by the deadline: the owner stays liable

Only offences that can fall on a driver are asked about. A vehicle offence such as an
expired fitness certificate stays with the owner whoever was behind the wheel.
"""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify
from app.services.ownership import DRIVER_LIABLE, owner_user_id, owned_vehicles_query, _linked_drivers

OPEN = ("requested",)


def _name(driver: Driver) -> str:
    return f"{driver.first_name} {driver.last_name}".strip()


def open_nomination(db: Session, challan: Challan, violation: Violation) -> bool:
    """Ask the owner who was driving, if that applies to this fine. Returns whether it was asked."""
    if violation.driver_id is not None or violation.liable_party not in DRIVER_LIABLE:
        return False
    vehicle = db.get(Vehicle, challan.vehicle_id) if challan.vehicle_id else None
    owner_id = owner_user_id(db, vehicle)
    if owner_id is None:
        return False
    days = runtime_settings.get(db, "enforcement.nomination_days")
    challan.nomination_status = "requested"
    challan.nomination_deadline = datetime.utcnow() + timedelta(days=days)
    notify(
        db, owner_id, "driver_nomination_requested",
        {
            "reference": challan.reference,
            "registration": vehicle.registration_number,
            "place": violation.location or "an unrecorded place",
            "when": violation.timestamp.strftime("%d %b %Y %H:%M") if violation.timestamp else "an unrecorded time",
            "deadline": challan.nomination_deadline.strftime("%d %b %Y"),
        },
    )
    return True


def _is_owner(db: Session, user: User, challan: Challan) -> bool:
    if challan.vehicle_id is None:
        return False
    return owned_vehicles_query(db, user).filter(Vehicle.id == challan.vehicle_id).first() is not None


def _my_driver_ids(db: Session, user: User) -> set:
    return {d.id for d in _linked_drivers(db, user)}


def _load(db: Session, challan_id) -> Challan:
    challan = db.get(Challan, challan_id)
    if challan is None:
        raise HTTPException(status_code=404, detail="Fine not found")
    return challan


def _assign_driver(db: Session, challan: Challan, driver: Driver) -> None:
    """Record the driver on the violation and fine, so the fine reaches them like any driver fine."""
    challan.driver_id = driver.id
    violation = db.get(Violation, challan.violation_id)
    if violation is not None:
        violation.driver_id = driver.id
        if violation.liable_party in DRIVER_LIABLE:
            from app.services.demerits import award_points

            award_points(db, violation, driver)


def nominate(db: Session, user: User, challan_id, licence_number: str, statement: str | None) -> Challan:
    challan = _load(db, challan_id)
    if not _is_owner(db, user, challan):
        raise HTTPException(status_code=404, detail="Fine not found")  # not theirs: don't confirm it exists
    if challan.status == ChallanStatus.PAID:
        raise HTTPException(status_code=409, detail="This fine has already been paid")
    if challan.nomination_status not in OPEN:
        raise HTTPException(status_code=409, detail="No driver is being asked for on this fine")
    if challan.nomination_deadline and challan.nomination_deadline < datetime.utcnow():
        raise HTTPException(status_code=409, detail="The time to name the driver has passed")

    number = (licence_number or "").strip().upper()
    driver = db.query(Driver).filter(func.upper(Driver.licence_number) == number).first() if number else None
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver is registered with that licence number")

    challan.nominated_driver_id = driver.id
    challan.nominated_by = user.id
    challan.nominated_at = datetime.utcnow()
    challan.nomination_statement = (statement or "").strip()[:500] or None
    log_action(db, "nominate_driver", "challan", str(challan.id),
               f"{challan.reference}: owner named licence {driver.licence_number}", user.id)

    if driver.id in _my_driver_ids(db, user):
        # The owner was driving: nothing to ask anyone.
        challan.nomination_status = "accepted"
        _assign_driver(db, challan, driver)
        return challan

    challan.nomination_status = "nominated"
    if driver.user_id:
        violation = db.get(Violation, challan.violation_id)
        notify(
            db, driver.user_id, "driver_named",
            {
                "reference": challan.reference,
                "owner": user.full_name,
                "place": (violation.location if violation else "") or "an unrecorded place",
                "when": violation.timestamp.strftime("%d %b %Y %H:%M") if violation and violation.timestamp else "an unrecorded time",
            },
        )
    return challan


def respond(db: Session, user: User, challan_id, accept: bool, note: str | None) -> Challan:
    challan = _load(db, challan_id)
    driver = db.get(Driver, challan.nominated_driver_id) if challan.nominated_driver_id else None
    if driver is None or driver.id not in _my_driver_ids(db, user):
        raise HTTPException(status_code=404, detail="Fine not found")
    if challan.nomination_status != "nominated":
        raise HTTPException(status_code=409, detail="This nomination has already been answered")
    challan.nominee_note = (note or "").strip()[:500] or None
    if accept:
        challan.nomination_status = "accepted"
        _assign_driver(db, challan, driver)
        event, outcome = "nomination_accepted", "accepted"
    else:
        challan.nomination_status = "rejected"
        event, outcome = "nomination_rejected", "rejected"
    log_action(db, f"nomination_{outcome}", "challan", str(challan.id),
               f"{challan.reference}: {driver.licence_number} {outcome} the nomination", user.id)
    if challan.nominated_by:
        notify(db, challan.nominated_by, event, {"reference": challan.reference, "driver": _name(driver)})
    return challan


def resolve(db: Session, officer: User, challan_id, decision: str, note: str | None) -> Challan:
    """An officer settles a rejected nomination: the fine goes to the driver or stays with the owner."""
    challan = _load(db, challan_id)
    if challan.nomination_status != "rejected":
        raise HTTPException(status_code=409, detail="Only a rejected nomination needs a decision")
    if decision == "driver":
        driver = db.get(Driver, challan.nominated_driver_id)
        challan.nomination_status = "accepted"
        _assign_driver(db, challan, driver)
    else:
        challan.nomination_status = "expired"
    log_action(db, "resolve_nomination", "challan", str(challan.id),
               f"{challan.reference}: officer decided for the {decision}. {(note or '').strip()[:200]}", officer.id)
    return challan


def expire_nominations(db: Session, now: datetime | None = None) -> int:
    """Owners who never answered stay liable. Returns how many requests lapsed."""
    now = now or datetime.utcnow()
    lapsed = db.query(Challan).filter(Challan.nomination_status == "requested", Challan.nomination_deadline < now).all()
    for c in lapsed:
        c.nomination_status = "expired"
    return len(lapsed)
