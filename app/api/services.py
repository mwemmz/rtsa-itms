"""Pay-for services: the things a citizen pays RTSA for, other than fines and tolls.

Each service has a server-side fee (admin-editable under Settings > fees). Paying
does the work the fee is for: books the test, renews the permit, or logs the
replacement card request. The charge goes through the same payment code as fines.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.portal import LICENCE_RENEWAL_FEE, _ensure_driver_registered
from app.core.database import get_db
from app.core.security import get_current_user
from app.models.insurance import Insurance
from app.models.licence import LicenceApplication, LicenceApplicationStatus
from app.models.inspection import FitnessCertificate, Inspection, InspectionResult
from app.models.payment import PaymentStatus, PaymentType
from app.models.psv import PSVPermit, PSVPermitStatus
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleStatus
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.money import zmw
from app.services.ownership import owner_user_id
from app.services.payments import STAFF, create_payment

router = APIRouter(prefix="/api/services", tags=["Services"])

CENTRES = ["Lusaka Main VID Centre", "Kitwe VID Centre", "Ndola VID Centre", "Livingstone VID Centre", "Kabwe VID Centre"]
FITNESS_RENEW_WINDOW_DAYS = 60
REGISTRATION_RENEW_WINDOW_DAYS = 60

SERVICES = [
    {"id": "fitness_test", "name": "Book a fitness test", "agency": "RTSA", "needs_vehicle": True,
     "fee_key": "fees.fitness_test",
     "what": "Pay the test fee and book a roadworthiness inspection. A pass issues your fitness certificate."},
    {"id": "psv_permit_renewal", "name": "Renew a PSV route permit", "agency": "RTSA", "needs_vehicle": True,
     "fee_key": "fees.psv_permit_renewal",
     "what": "Extend the route permit on a bus, minibus or taxi by one year."},
    {"id": "licence_renewal", "name": "Renew your driving licence", "agency": "RTSA", "needs_vehicle": False,
     "fee": LICENCE_RENEWAL_FEE, "link": "licence",
     "what": "Renewal opens 90 days before expiry. Done on the My licence page."},
    {"id": "vehicle_registration", "name": "Renew vehicle registration", "agency": "RTSA", "needs_vehicle": True,
     "fee_key": "fees.vehicle_registration_renewal",
     "what": "Pay the annual registration fee for a vehicle. Opens 60 days before it runs out and cannot be paid for a stolen vehicle."},
    {"id": "licence_application", "name": "Pay licence application fee", "agency": "RTSA", "needs_application": True,
     "fee_key": "fees.licence_application",
     "what": "Pay the application and test fee for a driving licence you applied for. Tests are scheduled once it is paid."},
    {"id": "duplicate_licence", "name": "Replace a lost licence card", "agency": "RTSA", "needs_vehicle": False,
     "fee_key": "fees.duplicate_licence",
     "what": "Request a replacement card. Collect it at the RTSA office you choose."},
]
NOT_HERE = [
    {"name": "Road tax", "agency": "ZRA", "what": "Paid to the Zambia Revenue Authority, not RTSA."},
    {"name": "Motor insurance", "agency": "Your insurer", "what": "Bought from an insurer. Officers record the policy here."},
]


class PayIn(BaseModel):
    service: str
    vehicle_id: str | None = None
    application_id: str | None = None
    inspection_centre: str | None = None
    preferred_date: datetime | None = None


def _fee(db: Session, svc: dict) -> int:
    return svc["fee"] if "fee" in svc else runtime_settings.get(db, svc["fee_key"])


@router.get("/catalogue")
def catalogue(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    apps = (db.query(LicenceApplication)
            .filter(LicenceApplication.applicant_id == current_user.id, LicenceApplication.fee_paid_at.is_(None),
                    LicenceApplication.status.notin_([LicenceApplicationStatus.ISSUED, LicenceApplicationStatus.REJECTED])).all())
    return {
        "unpaid_applications": [{"id": str(a.id), "label": f"{a.first_name} {a.last_name} · class {a.requested_class.value}"} for a in apps],
        "services": [
            {k: v for k, v in s.items() if k not in ("fee_key",)} | {"fee": _fee(db, s), "fee_label": zmw(_fee(db, s))}
            for s in SERVICES
        ],
        "centres": CENTRES,
        "not_handled_here": NOT_HERE,
    }


def _owned_vehicle(db: Session, user: User, vehicle_id: str | None) -> Vehicle:
    if not vehicle_id:
        raise HTTPException(status_code=400, detail="Choose a vehicle")
    vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None or not (user.role in STAFF or owner_user_id(db, vehicle) == user.id):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


@router.post("/pay")
def pay_for_service(payload: PayIn, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    svc = next((s for s in SERVICES if s["id"] == payload.service), None)
    if svc is None:
        raise HTTPException(status_code=404, detail="Unknown service")
    if svc["id"] == "licence_renewal":
        raise HTTPException(status_code=400, detail="Renew your licence on the My licence page")
    fee = _fee(db, svc)
    now = datetime.utcnow()
    outcome: dict = {}

    if svc["id"] == "fitness_test":
        vehicle = _owned_vehicle(db, current_user, payload.vehicle_id)
        cert = (
            db.query(FitnessCertificate).filter(FitnessCertificate.vehicle_id == vehicle.id)
            .order_by(FitnessCertificate.expiry_date.desc()).first()
        )
        if cert and cert.expiry_date - now > timedelta(days=FITNESS_RENEW_WINDOW_DAYS):
            raise HTTPException(
                status_code=409,
                detail=f"{vehicle.registration_number} has a fitness certificate valid until {cert.expiry_date:%d %b %Y}. Book within {FITNESS_RENEW_WINDOW_DAYS} days of expiry.",
            )
        if db.query(Inspection).filter(Inspection.vehicle_id == vehicle.id, Inspection.result == InspectionResult.PENDING).first():
            raise HTTPException(status_code=409, detail="A fitness test is already booked for this vehicle")
        centre = payload.inspection_centre or CENTRES[0]
        if centre not in CENTRES:
            raise HTTPException(status_code=400, detail="Choose one of the listed inspection centres")
        when = payload.preferred_date or (now + timedelta(days=3))
        if when < now:
            raise HTTPException(status_code=400, detail="Choose a date in the future")
        payment, _ = create_payment(db, current_user, PaymentType.FEE, vehicle.id, fee,
                                    description=f"Fitness test fee {vehicle.registration_number}")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=402, detail="Payment did not complete")
        insp = Inspection(vehicle_id=vehicle.id, inspection_centre=centre, scheduled_date=when)
        db.add(insp)
        outcome = {"message": f"Fitness test booked at {centre} on {when:%d %b %Y}", "inspection_id": str(insp.id) if insp.id else None}
        subject = vehicle.registration_number

    elif svc["id"] == "psv_permit_renewal":
        vehicle = _owned_vehicle(db, current_user, payload.vehicle_id)
        permit = (
            db.query(PSVPermit).filter(PSVPermit.vehicle_id == vehicle.id)
            .order_by(PSVPermit.expiry_date.desc()).first()
        )
        if permit is None:
            raise HTTPException(status_code=404, detail="No PSV permit on record for this vehicle. Apply at an RTSA office.")
        if permit.status == PSVPermitStatus.SUSPENDED:
            raise HTTPException(status_code=409, detail="This permit is suspended. Renewing cannot lift that: contact RTSA.")
        covered = db.query(Insurance).filter(
            Insurance.vehicle_id == vehicle.id, Insurance.is_active == True,  # noqa: E712
            Insurance.end_date >= now, Insurance.policy_type == "psv_passenger",
        ).first()
        if covered is None:
            raise HTTPException(
                status_code=409,
                detail="A PSV permit needs an active passenger-liability policy. Ask your insurer, then have an officer record it.",
            )
        payment, _ = create_payment(db, current_user, PaymentType.PERMIT, permit.id, fee,
                                    description=f"PSV permit renewal {permit.permit_number}")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=402, detail="Payment did not complete")
        permit.expiry_date = max(permit.expiry_date, now) + timedelta(days=365)
        permit.status = PSVPermitStatus.ACTIVE
        outcome = {"message": f"Permit {permit.permit_number} renewed until {permit.expiry_date:%d %b %Y}"}
        subject = permit.permit_number

    elif svc["id"] == "vehicle_registration":
        vehicle = _owned_vehicle(db, current_user, payload.vehicle_id)
        if vehicle.status == VehicleStatus.STOLEN or vehicle.is_blacklisted:
            raise HTTPException(status_code=409, detail="Registration cannot be renewed while the vehicle is flagged stolen or blacklisted. Contact RTSA.")
        current = vehicle.registration_expiry or (vehicle.registration_date.replace(tzinfo=None) + timedelta(days=365))
        current = current.replace(tzinfo=None)
        if current - now > timedelta(days=REGISTRATION_RENEW_WINDOW_DAYS):
            raise HTTPException(
                status_code=409,
                detail=f"{vehicle.registration_number} is registered until {current:%d %b %Y}. Renew within {REGISTRATION_RENEW_WINDOW_DAYS} days of that date.",
            )
        payment, _ = create_payment(db, current_user, PaymentType.FEE, vehicle.id, fee,
                                    description=f"Vehicle registration renewal {vehicle.registration_number}")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=402, detail="Payment did not complete")
        vehicle.registration_expiry = max(current, now) + timedelta(days=365)
        outcome = {"message": f"{vehicle.registration_number} registered until {vehicle.registration_expiry:%d %b %Y}"}
        subject = vehicle.registration_number

    elif svc["id"] == "licence_application":
        if not payload.application_id:
            raise HTTPException(status_code=400, detail="Choose an application")
        app_row = db.query(LicenceApplication).filter(
            LicenceApplication.id == payload.application_id, LicenceApplication.applicant_id == current_user.id).first()
        if app_row is None:
            raise HTTPException(status_code=404, detail="Application not found")
        if app_row.fee_paid_at is not None:
            raise HTTPException(status_code=409, detail="The fee for this application is already paid")
        if app_row.status in (LicenceApplicationStatus.ISSUED, LicenceApplicationStatus.REJECTED):
            raise HTTPException(status_code=409, detail=f"This application is already {app_row.status.value}")
        payment, _ = create_payment(db, current_user, PaymentType.FEE, app_row.id, fee,
                                    description=f"Licence application fee {app_row.first_name} {app_row.last_name}")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=402, detail="Payment did not complete")
        app_row.fee_paid_at = now
        outcome = {"message": "Application fee paid. The testing centre will schedule your theory test"}
        subject = f"{app_row.first_name} {app_row.last_name}"

    else:  # duplicate_licence
        driver = _ensure_driver_registered(db, current_user)
        payment, _ = create_payment(db, current_user, PaymentType.FEE, driver.id, fee,
                                    description=f"Replacement licence card {driver.licence_number}")
        if payment.status != PaymentStatus.COMPLETED:
            raise HTTPException(status_code=402, detail="Payment did not complete")
        outcome = {"message": "Replacement card requested. Collect it at your chosen RTSA office in 5 working days."}
        subject = driver.licence_number

    db.flush()
    if svc["id"] == "fitness_test":
        outcome["inspection_id"] = str(insp.id)
    log_action(db, "pay_service", "payment", str(payment.id), f"{svc['name']}: {subject}", current_user.id)
    db.commit()
    return {"ok": True, "service": svc["id"], "amount": fee, "receipt_number": payment.receipt_number,
            "payment_reference": payment.reference, **outcome}
