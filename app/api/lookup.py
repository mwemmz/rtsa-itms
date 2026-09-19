"""Toll-gate / roadside lookup: an officer enters a plate and sees, read-only,
whether the vehicle and its owner have anything pending.

Unlike ``POST /api/toll/events`` this records nothing and issues no fines, so it
is safe to run as often as needed. Each lookup is written to the audit log
(who looked up which plate) because it exposes personal data.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_role
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.services.audit import log_action
from app.services.compliance import check_vehicle_compliance

router = APIRouter(prefix="/api/toll", tags=["Toll"])

OPEN_STATUSES = (ChallanStatus.UNPAID, ChallanStatus.OVERDUE, ChallanStatus.DISPUTED)


def _normalise(plate: str) -> str:
    return "".join(plate.split()).upper()


@router.get("/lookup/{plate}")
def lookup_plate(
    plate: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.OFFICER, UserRole.TOLL_OPERATOR, UserRole.ADMIN)),
):
    """Look a vehicle up by plate ("BAL 1234" and "bal1234" both match)."""
    key = _normalise(plate)
    if len(key) < 3:
        raise HTTPException(status_code=422, detail="Enter at least 3 characters of the plate")
    vehicle = (
        db.query(Vehicle)
        .filter(func.replace(func.upper(Vehicle.registration_number), " ", "") == key)
        .first()
    )
    log_action(db, "plate_lookup", "vehicle", str(vehicle.id) if vehicle else None,
               f"{key}: {'found' if vehicle else 'not registered'}", current_user.id)
    db.commit()
    if vehicle is None:
        return {"found": False, "plate": key, "decision": "stop",
                "reasons": ["No vehicle is registered with this plate"], "offences": [], "total_owed": 0}

    now = datetime.utcnow()
    compliance = check_vehicle_compliance(db, vehicle)

    rows = (
        db.query(Challan, Violation)
        .outerjoin(Violation, Violation.id == Challan.violation_id)
        .filter(Challan.vehicle_id == vehicle.id, Challan.status.in_(OPEN_STATUSES))
        .order_by(Challan.due_date)
        .all()
    )
    offences = [
        {
            "challan_id": str(c.id),
            "reference": c.reference,
            "offence": v.violation_type.value if v else "unknown",
            "location": v.location if v else None,
            "committed_at": v.timestamp.isoformat() if v and v.timestamp else None,
            "amount": c.penalty_amount,
            "due_date": c.due_date.isoformat(),
            "overdue": c.due_date < now,
            "status": c.status.value,
        }
        for c, v in rows
    ]

    # the registered owner's licence, matched on national ID
    driver = db.query(Driver).filter(Driver.id_number == vehicle.owner_id_number).first()
    licence = None
    if driver:
        expired = driver.licence_expiry_date < now
        licence = {
            "licence_number": driver.licence_number,
            "class": driver.licence_class.value,
            "status": driver.status.value,
            "expires": driver.licence_expiry_date.isoformat(),
            "valid": driver.status.value == "active" and not expired,
        }

    reasons = list(compliance.issues)
    if licence and not licence["valid"]:
        reasons.append(f"Owner's licence is {'expired' if licence['status'] == 'active' else licence['status']}")
    return {
        "found": True,
        "plate": vehicle.registration_number,
        "vehicle": {
            "id": str(vehicle.id), "make": vehicle.make, "model": vehicle.model, "year": vehicle.year,
            "colour": vehicle.color, "status": vehicle.status.value,
            "blacklisted": vehicle.is_blacklisted, "blacklist_reason": vehicle.blacklist_reason,
        },
        "owner": vehicle.owner_name,
        "licence": licence,
        "checks": compliance.checks,
        "offences": offences,
        "offence_count": len(offences),
        "overdue_count": sum(1 for o in offences if o["overdue"]),
        "total_owed": sum(o["amount"] for o in offences),
        "decision": "allow" if not reasons else "stop",
        "reasons": reasons,
    }
