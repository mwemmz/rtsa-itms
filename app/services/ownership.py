"""Who owns a vehicle, in one place.

A vehicle belongs to a user account when either
* it was linked directly (``Vehicle.user_id``), or
* its registered owner's national ID matches a driver record linked to that
  account (so vehicles registered at an RTSA office show up for the owner as
  soon as they hold a licence, without anyone linking them by hand).

Never match on names: two people can share one, and the other would see (or be
notified about) this owner's vehicles and fines.
"""

import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.models.driver import Driver
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
