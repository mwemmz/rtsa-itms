"""Matching a typed or camera-read plate to a registered vehicle.

People and cameras write the same plate several ways ("BAL 1234", "bal1234",
"BAL  1234"), so plates are compared with spaces removed and in upper case.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.vehicle import Vehicle
from app.services.compliance import check_vehicle_compliance

UNREGISTERED_REASON = "No vehicle is registered with this plate"


def normalise_plate(plate: str) -> str:
    return "".join(plate.split()).upper()


def plate_key(column):
    """The SQL counterpart of normalise_plate, for comparing a plate column."""
    return func.replace(func.upper(column), " ", "")


def find_vehicle_by_plate(db: Session, plate: str) -> Vehicle | None:
    return db.query(Vehicle).filter(plate_key(Vehicle.registration_number) == normalise_plate(plate)).first()


def plate_alert(db: Session, vehicle: Vehicle | None) -> tuple[str, list[str]]:
    """("stop" | "allow", reasons) for a vehicle seen on the road.

    An unregistered plate is a stop in itself: it may be false or cloned.
    """
    if vehicle is None:
        return "stop", [UNREGISTERED_REASON]
    issues = list(check_vehicle_compliance(db, vehicle).issues)
    return ("stop" if issues else "allow"), issues
