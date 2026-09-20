"""Citizen self-service portal: my vehicles, licence, fines, applications, payments."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.inspection import FitnessCertificate
from app.models.insurance import Insurance
from app.models.licence import LicenceApplication, LicenceApplicationStatus
from app.models.payment import Payment
from app.models.psv import PSVPermit
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.citizen import CitizenDashboard, CitizenFinesSummary
from app.schemas.driver import DriverResponse
from app.schemas.enforcement import ChallanResponse
from app.schemas.payment import PaymentResponse
from app.schemas.vehicle import VehicleResponse
from app.services.audit import log_action
from app.services.compliance import check_vehicle_compliance

router = APIRouter(prefix="/api/citizen", tags=["Citizen Portal"])

# Ordered steps of the licence journey, used to draw a progress tracker.
LICENCE_STEPS = [
    (LicenceApplicationStatus.SUBMITTED, "Application submitted"),
    (LicenceApplicationStatus.THEORY_TEST_SCHEDULED, "Theory test scheduled"),
    (LicenceApplicationStatus.THEORY_TEST_PASSED, "Theory test passed"),
    (LicenceApplicationStatus.PRACTICAL_TEST_SCHEDULED, "Practical test scheduled"),
    (LicenceApplicationStatus.PRACTICAL_TEST_PASSED, "Practical test passed"),
    (LicenceApplicationStatus.ISSUED, "Licence issued"),
]
_FAILED = {
    LicenceApplicationStatus.THEORY_TEST_FAILED: LicenceApplicationStatus.THEORY_TEST_PASSED,
    LicenceApplicationStatus.PRACTICAL_TEST_FAILED: LicenceApplicationStatus.PRACTICAL_TEST_PASSED,
}


def my_drivers(db: Session, user: User) -> list[Driver]:
    rows = db.query(Driver).filter(Driver.user_id == user.id).all()
    if not rows:
        rows = db.query(Driver).filter(Driver.email == user.email).all()
    return rows


def my_vehicles_query(db: Session, user: User):
    """Vehicles linked to the account directly, or through its driver record's national ID."""
    ids = [d.id_number for d in my_drivers(db, user)]
    cond = Vehicle.user_id == user.id
    if ids:
        cond = cond | Vehicle.owner_id_number.in_(ids)
    return db.query(Vehicle).filter(cond)


def _my_vehicle_ids(db: Session, user: User) -> list:
    return [v.id for v in my_vehicles_query(db, user).with_entities(Vehicle.id).all()]


def _fines_query(db: Session, user: User):
    ids = _my_vehicle_ids(db, user)
    if not ids:
        return db.query(Challan).filter(Challan.id.is_(None))  # empty result
    return db.query(Challan).filter(Challan.vehicle_id.in_(ids))


@router.get("/dashboard", response_model=CitizenDashboard)
def citizen_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicles = my_vehicles_query(db, current_user).all()
    licences = my_drivers(db, current_user)
    challans = _fines_query(db, current_user).order_by(Challan.created_at.desc()).all()
    unpaid = [c for c in challans if c.status in (ChallanStatus.UNPAID, ChallanStatus.OVERDUE)]
    return CitizenDashboard(
        vehicles=[VehicleResponse.model_validate(v).model_dump(mode="json") for v in vehicles],
        licences=[DriverResponse.model_validate(d).model_dump(mode="json") for d in licences],
        fines=CitizenFinesSummary(
            total_unpaid=len(unpaid),
            total_amount=sum(c.penalty_amount for c in unpaid),
            challans=[ChallanResponse.model_validate(c).model_dump(mode="json") for c in challans],
        ),
    )


@router.get("/summary")
def citizen_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """One-glance status: what is owed and what is about to expire."""
    now = datetime.utcnow()
    horizon = now + timedelta(days=60)
    vehicles = my_vehicles_query(db, current_user).all()
    vids = [v.id for v in vehicles]
    upcoming = []
    for d in my_drivers(db, current_user):
        if d.licence_expiry_date <= horizon:
            upcoming.append({"type": "licence", "ref": d.licence_number, "expires": d.licence_expiry_date.isoformat(),
                             "days_left": (d.licence_expiry_date.date() - now.date()).days})
    if vids:
        for ins, veh in db.query(Insurance, Vehicle).join(Vehicle, Vehicle.id == Insurance.vehicle_id).filter(
                Insurance.vehicle_id.in_(vids), Insurance.is_active == True, Insurance.end_date <= horizon):  # noqa: E712
            upcoming.append({"type": "insurance", "ref": veh.registration_number, "expires": ins.end_date.isoformat(),
                             "days_left": (ins.end_date.date() - now.date()).days})
        for cert, veh in db.query(FitnessCertificate, Vehicle).join(
                Vehicle, Vehicle.id == FitnessCertificate.vehicle_id).filter(
                FitnessCertificate.vehicle_id.in_(vids), FitnessCertificate.expiry_date <= horizon):
            upcoming.append({"type": "fitness", "ref": veh.registration_number,
                             "expires": cert.expiry_date.isoformat(),
                             "days_left": (cert.expiry_date.date() - now.date()).days})
    unpaid = _fines_query(db, current_user).filter(
        Challan.status.in_([ChallanStatus.UNPAID, ChallanStatus.OVERDUE])).all()
    return {
        "vehicles": len(vehicles),
        "licences": len(my_drivers(db, current_user)),
        "unpaid_fines": len(unpaid),
        "amount_due": sum(c.penalty_amount for c in unpaid),
        "overdue_fines": sum(1 for c in unpaid if c.due_date < now),
        "expiring_soon": sorted(upcoming, key=lambda x: x["days_left"]),
        "open_applications": db.query(LicenceApplication).filter(
            LicenceApplication.applicant_id == current_user.id,
            LicenceApplication.status.notin_([LicenceApplicationStatus.ISSUED, LicenceApplicationStatus.REJECTED]),
        ).count(),
    }


@router.get("/my-vehicles", response_model=list[VehicleResponse])
def my_vehicles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return my_vehicles_query(db, current_user).order_by(Vehicle.registration_date.desc()).all()


@router.get("/vehicles/{vehicle_id}")
def vehicle_detail(
    vehicle_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A vehicle's compliance picture: insurance, fitness, permits and fines."""
    vehicle = my_vehicles_query(db, current_user).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    compliance = check_vehicle_compliance(db, vehicle)
    insurance = (db.query(Insurance).filter(Insurance.vehicle_id == vehicle.id)
                 .order_by(Insurance.end_date.desc()).first())
    fitness = (db.query(FitnessCertificate).filter(FitnessCertificate.vehicle_id == vehicle.id)
               .order_by(FitnessCertificate.expiry_date.desc()).first())
    permit = (db.query(PSVPermit).filter(PSVPermit.vehicle_id == vehicle.id)
              .order_by(PSVPermit.expiry_date.desc()).first())
    fines = db.query(Challan).filter(Challan.vehicle_id == vehicle.id).order_by(Challan.created_at.desc()).all()
    return {
        "vehicle": VehicleResponse.model_validate(vehicle).model_dump(mode="json"),
        "compliant": compliance.compliant,
        "issues": compliance.issues,
        "checks": compliance.checks,
        "insurance": None if insurance is None else {
            "provider": insurance.provider, "policy_number": insurance.policy_number,
            "valid_until": insurance.end_date.isoformat(), "active": insurance.is_active},
        "fitness": None if fitness is None else {
            "certificate_number": fitness.certificate_number, "valid_until": fitness.expiry_date.isoformat()},
        "psv_permit": None if permit is None else {
            "permit_number": permit.permit_number, "route": permit.route,
            "valid_until": permit.expiry_date.isoformat(), "status": permit.status.value},
        "fines": [ChallanResponse.model_validate(c).model_dump(mode="json") for c in fines],
    }


@router.get("/my-fines", response_model=list[ChallanResponse])
def my_fines(
    status: ChallanStatus | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = _fines_query(db, current_user)
    if status:
        query = query.filter(Challan.status == status)
    return query.order_by(Challan.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/fines/{challan_id}")
def fine_detail(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = _fines_query(db, current_user).filter(Challan.id == challan_id).first()
    if challan is None:
        raise HTTPException(status_code=404, detail="Fine not found")
    violation = db.get(Violation, challan.violation_id)
    payments = db.query(Payment).filter(Payment.related_entity_id == challan.id).order_by(Payment.created_at).all()
    return {
        "challan": ChallanResponse.model_validate(challan).model_dump(mode="json"),
        "violation": None if violation is None else {
            "type": violation.violation_type.value, "location": violation.location,
            "time": violation.timestamp.isoformat(), "description": violation.description},
        "overdue": challan.status != ChallanStatus.PAID and challan.due_date < datetime.utcnow(),
        "payments": [PaymentResponse.model_validate(p).model_dump(mode="json") for p in payments],
    }


@router.get("/licence", response_model=list[DriverResponse])
def my_licences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return my_drivers(db, current_user)


# --- application tracking ------------------------------------------------------------

def _timeline(app: LicenceApplication) -> dict:
    status = app.status
    failed = status in _FAILED
    rejected = status == LicenceApplicationStatus.REJECTED
    order = [s for s, _ in LICENCE_STEPS]
    reference = _FAILED.get(status, status)
    idx = order.index(reference) if reference in order else 0
    steps = []
    for i, (s, label) in enumerate(LICENCE_STEPS):
        if failed and i == idx:
            state = "failed"
        elif rejected:
            state = "done" if i == 0 else "pending"
        elif i < idx or (status == LicenceApplicationStatus.ISSUED):
            state = "done"
        elif i == idx:
            state = "current"
        else:
            state = "pending"
        steps.append({"key": s.value, "label": label, "state": state})
    next_step = None
    if not failed and not rejected and status != LicenceApplicationStatus.ISSUED and idx + 1 < len(LICENCE_STEPS):
        next_step = LICENCE_STEPS[idx + 1][1]
    if failed:
        next_step = "Re-apply or contact RTSA to rebook the test"
    return {"steps": steps, "next_step": next_step, "terminal": status in (
        LicenceApplicationStatus.ISSUED, LicenceApplicationStatus.REJECTED)}


def _app_out(app: LicenceApplication) -> dict:
    return {
        "id": str(app.id),
        "type": "licence_application",
        "reference": f"LA-{str(app.id)[:8].upper()}",
        "requested_class": app.requested_class.value,
        "status": app.status.value,
        "theory_score": app.theory_score,
        "practical_score": app.practical_score,
        "issued_licence_number": app.issued_licence_number,
        "submitted_at": app.created_at.isoformat() if app.created_at else None,
        "updated_at": app.updated_at.isoformat() if app.updated_at else None,
        **_timeline(app),
    }


@router.get("/applications")
def my_applications(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    apps = (db.query(LicenceApplication).filter(LicenceApplication.applicant_id == current_user.id)
            .order_by(LicenceApplication.created_at.desc()).all())
    return [_app_out(a) for a in apps]


@router.get("/applications/{application_id}")
def track_application(
    application_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    app = (db.query(LicenceApplication)
           .filter(LicenceApplication.id == application_id, LicenceApplication.applicant_id == current_user.id)
           .first())
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found")
    return _app_out(app)


# --- payments & profile -------------------------------------------------------------------

@router.get("/payments", response_model=list[PaymentResponse])
def my_payments(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (db.query(Payment).filter(Payment.paid_by == current_user.id)
            .order_by(Payment.created_at.desc()).offset(skip).limit(limit).all())


@router.get("/profile")
def profile(current_user: User = Depends(get_current_user)):
    return {"email": current_user.email, "full_name": current_user.full_name,
            "phone_number": current_user.phone_number, "mfa_enabled": current_user.mfa_enabled,
            "member_since": current_user.created_at.isoformat() if current_user.created_at else None}


@router.patch("/profile")
def update_profile(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    name = payload.get("full_name")
    phone = payload.get("phone_number")
    if name is not None:
        if not str(name).strip() or len(str(name)) > 200:
            raise HTTPException(status_code=422, detail="Invalid name")
        current_user.full_name = str(name).strip()
    if phone is not None:
        phone = str(phone).strip()
        digits = phone.lstrip("+").replace(" ", "").replace("-", "")
        if phone and (not digits.isdigit() or not 9 <= len(digits) <= 15):
            raise HTTPException(status_code=422, detail="Enter a valid phone number, e.g. +260971234567")
        current_user.phone_number = phone or None
    log_action(db, "update_profile", "user", str(current_user.id), None, current_user.id)
    db.commit()
    return profile(current_user)
