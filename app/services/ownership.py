"""Who owns a vehicle, in one place.

A vehicle belongs to a user account when either
* it was linked directly (``Vehicle.user_id``), or
* its registered owner's national ID matches a driver record linked to that
  account (so vehicles registered at an RTSA office show up for the owner as
  soon as they hold a licence, without anyone linking them by hand).

Never match on names: two people can share one, and the other would see (or be
notified about) this owner's vehicles and fines.

A fine (e-Challan) is owed by a user when it is charged to one of their vehicles,
to their account directly (a false road report has no vehicle to charge), or to
them as the driver of an offence the driver answers for (a fine recorded against
a licence, with or without a vehicle).
"""

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.driver import Driver
from app.models.enforcement import Challan, Violation
from app.models.user import User
from app.models.vehicle import Vehicle

# Liability on a violation that falls on whoever was driving. A vehicle offence
# ("owner") stays with the owner even when the driver is on the record.
DRIVER_LIABLE = ("driver", "both")


def _linked_drivers(db: Session, user: User) -> Query:
    """The user's driver records: linked to the account, or carrying its email."""
    return db.query(Driver).filter(or_(Driver.user_id == user.id, Driver.email == user.email))


def owned_vehicles_query(db: Session, user: User) -> Query:
    national_ids = [d.id_number for d in _linked_drivers(db, user).with_entities(Driver.id_number)]
    cond = Vehicle.user_id == user.id
    if national_ids:
        cond = or_(cond, Vehicle.owner_id_number.in_(national_ids))
    return db.query(Vehicle).filter(cond)


def owner_user_id(db: Session, vehicle: Vehicle | None) -> uuid.UUID | None:
    if vehicle is None:
        return None
    if vehicle.user_id:
        return vehicle.user_id
    driver = (
        db.query(Driver)
        .filter(Driver.id_number == vehicle.owner_id_number, Driver.user_id.isnot(None))
        .first()
    )
    return driver.user_id if driver else None


def owed_challans_query(db: Session, user: User) -> Query:
    """Every e-Challan this user is liable to pay, paid or not."""
    vehicle_ids = owned_vehicles_query(db, user).with_entities(Vehicle.id)
    owed = [Challan.vehicle_id.in_(vehicle_ids), Challan.user_id == user.id]
    driver_ids = [d.id for d in _linked_drivers(db, user).with_entities(Driver.id)]
    if driver_ids:
        # Fines for offences they committed as the driver, including ones recorded
        # against their licence with no vehicle, which nothing above would reach.
        as_driver = db.query(Violation.id).filter(
            Violation.driver_id.in_(driver_ids), Violation.liable_party.in_(DRIVER_LIABLE)
        )
        owed.append(Challan.violation_id.in_(as_driver))
    return db.query(Challan).filter(or_(*owed))


def owes_challan(db: Session, user: User, challan: Challan | None) -> bool:
    """Whether the user may pay this fine: exactly the fines the portal lists as theirs.

    Deciding it from the same query keeps the two from drifting apart, as they
    had: an owner linked only by email saw their vehicle's fines but was refused
    when they tried to pay one.
    """
    if challan is None:
        return False
    return owed_challans_query(db, user).filter(Challan.id == challan.id).first() is not None
