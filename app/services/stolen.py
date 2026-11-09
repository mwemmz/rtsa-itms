"""Stolen vehicles: the owner reports the theft; fines for offences after it are waived and the sightings go to the police.

Without this, a thief running a toll gate in a stolen car produced a fine for the rightful owner.
"""

from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleStatus
from app.services.audit import log_action
from app.services.notifications import broadcast
from app.services.ownership import owned_vehicles_query


def stolen_at(vehicle: Vehicle | None, when: datetime | None) -> bool:
    """Was the vehicle reported stolen at the time of an offence (and not yet recovered)?"""
    if vehicle is None or vehicle.stolen_reported_at is None:
        return False
    when = when or datetime.utcnow()
    if when.tzinfo is not None:
        when = when.replace(tzinfo=None)
    if when < vehicle.stolen_reported_at:
        return False
    return vehicle.recovered_at is None or when < vehicle.recovered_at


def waive_if_stolen(db: Session, challan: Challan, violation: Violation, vehicle: Vehicle | None) -> bool:
    """Cancel a new fine on a vehicle that was stolen at the time. The owner is not charged or told."""
    if not stolen_at(vehicle, violation.timestamp):
        return False
    challan.status = ChallanStatus.CANCELLED
    challan.nomination_status = "none"
    log_action(db, "challan_waived_stolen", "challan", str(challan.id),
               f"{challan.reference} waived: {vehicle.registration_number} was reported stolen "
               f"(police report {vehicle.police_report_number})", None)
    return True


def sighting_alert(db: Session, vehicle: Vehicle, place: str) -> None:
    """Tell officers and admins a stolen vehicle has been seen."""
    broadcast(db, "stolen_vehicle_sighted",
              {"registration": vehicle.registration_number, "place": place, "report": vehicle.police_report_number or "n/a"},
              roles=["officer", "admin"])


def _new_reference(now: datetime) -> str:
    """System-issued theft reference, e.g. THEFT-20261109-A1B2C3. The owner never has to invent one."""
    import secrets
    return f"THEFT-{now:%Y%m%d}-{secrets.token_hex(3).upper()}"


def report_stolen(db: Session, user: User, vehicle_id, police_report_number: str | None, when: datetime | None) -> Vehicle:
    vehicle = owned_vehicles_query(db, user).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if vehicle.status == VehicleStatus.STOLEN:
        raise HTTPException(status_code=409, detail="This vehicle is already reported stolen")
    now = datetime.utcnow()
    when = when.replace(tzinfo=None) if when and when.tzinfo else when
    if when and when > now:
        raise HTTPException(status_code=422, detail="The theft cannot be in the future")
    vehicle.status = VehicleStatus.STOLEN
    vehicle.stolen_reported_at = when or now
    vehicle.police_report_number = (police_report_number or "").strip() or _new_reference(now)
    vehicle.recovered_at = None
    log_action(db, "report_stolen", "vehicle", str(vehicle.id),
               f"{vehicle.registration_number} reported stolen, police report {vehicle.police_report_number}", user.id)

    # Fines already issued for offences after the theft are not the owner's.
    waived = 0
    for challan in db.query(Challan).filter(Challan.vehicle_id == vehicle.id,
                                            Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE])).all():
        violation = db.get(Violation, challan.violation_id)
        if violation and violation.timestamp and violation.timestamp.replace(tzinfo=None) >= vehicle.stolen_reported_at:
            challan.status = ChallanStatus.CANCELLED
            challan.nomination_status = "none"
            log_action(db, "challan_waived_stolen", "challan", str(challan.id),
                       f"{challan.reference} waived after theft report", user.id)
            waived += 1
    broadcast(db, "stolen_vehicle_reported",
              {"registration": vehicle.registration_number, "report": vehicle.police_report_number},
              roles=["officer", "admin"])
    vehicle._waived = waived  # type: ignore[attr-defined]
    return vehicle


def mark_recovered(db: Session, officer: User, vehicle_id) -> Vehicle:
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.status != VehicleStatus.STOLEN:
        raise HTTPException(status_code=404, detail="No stolen vehicle with that id")
    vehicle.status = VehicleStatus.ACTIVE
    vehicle.recovered_at = datetime.utcnow()
    log_action(db, "vehicle_recovered", "vehicle", str(vehicle.id), f"{vehicle.registration_number} recovered", officer.id)
    return vehicle
