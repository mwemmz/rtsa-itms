from datetime import datetime

from sqlalchemy.orm import Session

from app.models.vehicle import Vehicle, VehicleStatus


class VehicleComplianceStatus:
    def __init__(self):
        self.issues: list[str] = []
        self.checks: list[dict] = []
        self.compliant = True


def check_vehicle_compliance(db: Session, vehicle: Vehicle) -> VehicleComplianceStatus:
    result = VehicleComplianceStatus()
    now = datetime.utcnow()

    # 1. Registration status
    if vehicle.status != VehicleStatus.ACTIVE:
        result.issues.append(f"Vehicle status is {vehicle.status.value}")
        result.checks.append({
            "check": "registration_status",
            "status": "fail",
            "detail": f"Status is {vehicle.status.value}",
        })
    else:
        result.checks.append({"check": "registration_status", "status": "pass"})

    # 2. Blacklist check
    if vehicle.is_blacklisted:
        result.issues.append("Vehicle is blacklisted")
        result.checks.append({
            "check": "blacklist",
            "status": "fail",
            "detail": vehicle.blacklist_reason or "Blacklisted",
        })
    else:
        result.checks.append({"check": "blacklist", "status": "pass"})

    # 3. Insurance validity
    from app.models.insurance import Insurance
    active_insurance = (
        db.query(Insurance)
        .filter(
            Insurance.vehicle_id == vehicle.id,
            Insurance.is_active == True,
            Insurance.end_date >= now,
        )
        .first()
    )
    if not active_insurance:
        result.issues.append("No valid insurance")
        result.checks.append({"check": "insurance", "status": "fail", "detail": "No active insurance"})
    else:
        result.checks.append({
            "check": "insurance",
            "status": "pass",
            "detail": f"Valid until {active_insurance.end_date}",
        })

    # 4. Fitness certificate
    from app.models.inspection import FitnessCertificate
    fitness = (
        db.query(FitnessCertificate)
        .filter(
            FitnessCertificate.vehicle_id == vehicle.id,
            FitnessCertificate.expiry_date >= now,
        )
        .order_by(FitnessCertificate.expiry_date.desc())
        .first()
    )
    if not fitness:
        result.issues.append("No valid fitness certificate")
        result.checks.append({
            "check": "fitness",
            "status": "fail",
            "detail": "No current fitness certificate",
        })
    else:
        result.checks.append({
            "check": "fitness",
            "status": "pass",
            "detail": f"Valid until {fitness.expiry_date}",
        })

    # 5. Outstanding fines/challans
    from app.models.enforcement import Challan, ChallanStatus
    outstanding = (
        db.query(Challan)
        .filter(
            Challan.vehicle_id == vehicle.id,
            Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE, ChallanStatus.DISPUTED]),
        )
        .count()
    )
    if outstanding > 0:
        result.issues.append(f"{outstanding} outstanding challan(s)")
        result.checks.append({
            "check": "outstanding_fines",
            "status": "fail",
            "detail": f"{outstanding} outstanding",
        })
    else:
        result.checks.append({"check": "outstanding_fines", "status": "pass"})

    # 6. PSV permit check
    from app.models.psv import PSVPermit, PSVPermitStatus
    psv_permit = (
        db.query(PSVPermit)
        .filter(
            PSVPermit.vehicle_id == vehicle.id,
            PSVPermit.status == PSVPermitStatus.ACTIVE,
            PSVPermit.expiry_date >= now,
        )
        .first()
    )
    if psv_permit:
        result.checks.append({
            "check": "psv_permit",
            "status": "pass",
            "detail": f"Permit {psv_permit.permit_number} valid until {psv_permit.expiry_date}",
        })
    else:
        # Only flag if the vehicle appears PSV-related (has a permit record historically)
        has_psv_history = db.query(PSVPermit.id).filter(PSVPermit.vehicle_id == vehicle.id).first()
        if has_psv_history:
            result.issues.append("No valid PSV permit")
            result.checks.append({
                "check": "psv_permit",
                "status": "fail",
                "detail": "No active PSV permit",
            })
        else:
            result.checks.append({
                "check": "psv_permit",
                "status": "pass",
                "detail": "Not a PSV vehicle",
            })

    if result.issues:
        result.compliant = False

    return result
