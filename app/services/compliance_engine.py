"""
Toll & ANPR Compliance Engine.

Checks a vehicle plate against all relevant compliance rules:
  - Vehicle registration status (blacklisted / stolen / deregistered)
  - Active insurance
  - Valid roadworthy certificate (passed inspection)
  - Outstanding unpaid fines
  - Active PSV permit (if PSV vehicle)
  - Outstanding toll debt

Used by the ANPR capture endpoint and the toll transit endpoint.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class ComplianceFlag(str, enum.Enum):
    BLACKLISTED = "BLACKLISTED"
    STOLEN = "STOLEN"
    EXPIRED_REGISTRATION = "EXPIRED_REGISTRATION"
    DEREGISTERED = "DEREGISTERED"
    EXPIRED_INSURANCE = "EXPIRED_INSURANCE"
    FAILED_INSPECTION = "FAILED_INSPECTION"
    OUTSTANDING_FINES = "OUTSTANDING_FINES"
    OUTSTANDING_TOLL_DEBT = "OUTSTANDING_TOLL_DEBT"
    PSV_PERMIT_EXPIRED = "PSV_PERMIT_EXPIRED"
    PSV_PERMIT_MISSING = "PSV_PERMIT_MISSING"


@dataclass
class ComplianceResult:
    plate_number: str
    vehicle_found: bool
    compliant: bool
    flags: list[ComplianceFlag] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    vehicle_id: str | None = None
    owner_nrc: str | None = None
    vehicle_status: str | None = None


def check_plate(db: "Session", plate_number: str) -> ComplianceResult:
    """
    Run all compliance checks for a given plate number.
    Returns a ComplianceResult with all flags populated.
    """
    from app.models.vehicles import Vehicle, VehicleStatus, VehicleCategory
    from app.models.insurance import InsurancePolicy, InsuranceStatus
    from app.models.inspections import Inspection, InspectionStatus
    from app.models.violations import Violation, ViolationStatus
    from app.models.toll import TollRecord, TollPaymentStatus
    from app.models.psv import PSVPermit, PSVPermitStatus

    plate = plate_number.upper().strip()
    now = datetime.now(timezone.utc)

    vehicle = db.query(Vehicle).filter(Vehicle.plate_number == plate).first()

    if not vehicle:
        return ComplianceResult(
            plate_number=plate,
            vehicle_found=False,
            compliant=False,
            flags=[],
            notes=["Vehicle not found in registry."],
        )

    result = ComplianceResult(
        plate_number=plate,
        vehicle_found=True,
        compliant=True,
        vehicle_id=vehicle.id,
        owner_nrc=vehicle.owner_nrc,
        vehicle_status=vehicle.status.value,
    )

    def _flag(f: ComplianceFlag, note: str):
        result.compliant = False
        result.flags.append(f)
        result.notes.append(note)

    # 1. Vehicle registration status
    if vehicle.status == VehicleStatus.BLACKLISTED:
        _flag(ComplianceFlag.BLACKLISTED, f"Blacklisted: {vehicle.blacklist_reason or 'no reason given'}")
    elif vehicle.status == VehicleStatus.STOLEN:
        _flag(ComplianceFlag.STOLEN, "Vehicle reported stolen.")
    elif vehicle.status == VehicleStatus.DEREGISTERED:
        _flag(ComplianceFlag.DEREGISTERED, "Vehicle is deregistered.")

    # 2. Registration expiry
    if vehicle.registration_expiry:
        exp = vehicle.registration_expiry
        exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
        if exp_naive < now.replace(tzinfo=None):
            _flag(ComplianceFlag.EXPIRED_REGISTRATION, f"Registration expired on {exp_naive.date()}.")

    # 3. Insurance
    active_insurance = (
        db.query(InsurancePolicy)
        .filter(
            InsurancePolicy.vehicle_id == vehicle.id,
            InsurancePolicy.status == InsuranceStatus.ACTIVE,
        )
        .first()
    )
    if not active_insurance:
        _flag(ComplianceFlag.EXPIRED_INSURANCE, "No active insurance policy found.")

    # 4. Roadworthy / inspection
    if not vehicle.is_roadworthy:
        _flag(ComplianceFlag.FAILED_INSPECTION, "Vehicle marked as not roadworthy.")

    # 5. Outstanding fines
    unpaid_count = (
        db.query(Violation)
        .filter(
            Violation.vehicle_id == vehicle.id,
            Violation.status == ViolationStatus.UNPAID,
        )
        .count()
    )
    if unpaid_count > 0:
        _flag(ComplianceFlag.OUTSTANDING_FINES, f"{unpaid_count} unpaid violation(s).")

    # 6. Outstanding toll debt
    unpaid_toll = (
        db.query(TollRecord)
        .filter(
            TollRecord.vehicle_id == vehicle.id,
            TollRecord.payment_status == TollPaymentStatus.UNPAID,
            TollRecord.amount_ngwee > 0,
        )
        .count()
    )
    if unpaid_toll > 0:
        _flag(ComplianceFlag.OUTSTANDING_TOLL_DEBT, f"{unpaid_toll} unpaid toll record(s).")

    # 7. PSV permit (only for PSV-category vehicles)
    if vehicle.category and vehicle.category.value == "PSV":
        active_psv = (
            db.query(PSVPermit)
            .filter(
                PSVPermit.vehicle_id == vehicle.id,
                PSVPermit.status == PSVPermitStatus.ACTIVE,
            )
            .first()
        )
        if not active_psv:
            _flag(ComplianceFlag.PSV_PERMIT_MISSING, "No active PSV permit.")
        else:
            exp = active_psv.valid_to
            exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
            if exp_naive < now.replace(tzinfo=None):
                _flag(ComplianceFlag.PSV_PERMIT_EXPIRED, f"PSV permit expired on {exp_naive.date()}.")

    return result
