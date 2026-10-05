"""Who owns a vehicle, in one place.

A vehicle belongs to a user account when either
* it was linked directly (``Vehicle.user_id``), or
* its registered owner's national ID matches a driver record linked to that
  account (so vehicles registered at an RTSA office show up for the owner as
  soon as they hold a licence, without anyone linking them by hand).

Never match on names: two people can share one, and the other would see (or be
notified about) this owner's vehicles and fines.

A fine (e-Challan) is owed by a user when it is charged to one of their vehicles,
or to their account directly (a false road report has no vehicle to charge).
"""

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.driver import Driver
from app.models.enforcement import Challan
from app.models.user import User
from app.models.vehicle import Vehicle


def owned_vehicles_query(db: Session, user: User) -> Query:
    national_ids = [
        d.id_number
        for d in db.query(Driver.id_number).filter(or_(Driver.user_id == user.id, Driver.email == user.email))
    ]
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
    return db.query(Challan).filter(or_(Challan.vehicle_id.in_(vehicle_ids), Challan.user_id == user.id))


def owes_challan(db: Session, user: User, challan: Challan | None) -> bool:
    if challan is None:
        return False
    if challan.user_id is not None and challan.user_id == user.id:
        return True
    return challan.vehicle_id is not None and owner_user_id(db, db.get(Vehicle, challan.vehicle_id)) == user.id
