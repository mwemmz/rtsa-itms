from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import create_access_token, verify_password, get_current_user
from app.models.driver import Driver, DriverStatus
from app.models.enforcement import Challan, ChallanStatus, Violation
from app.models.notification import Notification
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.road_network import Road, RoadIncident
from app.models.user import User
from app.models.vehicle import Vehicle
from app.schemas.portal import (
    FineOut,
    LicenceStatusOut,
    PortalDashboard,
    RouteAlertOut,
)
from app.services.audit import log_action
from app.services.notifications import notify
from app.api.payments import generate_payment_reference

router = APIRouter(prefix="/api/portal", tags=["Driver Portal"])

LICENCE_RENEWAL_FEE = 350000
EXPIRY_WARNING_DAYS = 30


def _licence_state(driver: Driver) -> tuple[str, int]:
    today = datetime.utcnow().date()
    expiry = driver.licence_expiry_date.date()
    days_left = (expiry - today).days
    if days_left < 0:
        state = "expired"
    elif days_left <= EXPIRY_WARNING_DAYS:
        state = "expiring_soon"
    else:
        state = "valid"
    return state, days_left


def _get_my_licence(db: Session, user: User) -> Driver | None:
    return db.query(Driver).filter(Driver.user_id == user.id).first()


def _get_my_vehicles(db: Session, user: User) -> list[Vehicle]:
    return (
        db.query(Vehicle)
        .filter(Vehicle.user_id == user.id)
        .order_by(Vehicle.registration_date.desc())
        .all()
    )


def _ensure_driver_registered(db: Session, user: User) -> Driver:
    driver = _get_my_licence(db, user)
    if not driver:
        raise HTTPException(
            status_code=404,
            detail="No driver record linked to this account. Contact RTSA or use the licence application flow.",
        )
    return driver


@router.get("/dashboard", response_model=PortalDashboard)
def dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = _get_my_licence(db, current_user)
    licence = None
    if driver:
        state, days = _licence_state(driver)
        licence = LicenceStatusOut(
            licence_number=driver.licence_number,
            licence_class=driver.licence_class,
            status=driver.status,
            issue_date=driver.licence_issue_date,
            expiry_date=driver.licence_expiry_date,
            days_until_expiry=days,
            state=state,
            restrictions=driver.restrictions,
        )

    vehicles = _get_my_vehicles(db, current_user)
    vehicle_ids = [v.id for v in vehicles]
    fines = (
        db.query(Challan)
        .filter(
            Challan.vehicle_id.in_(vehicle_ids) if vehicle_ids
            else Challan.vehicle_id.is_(None)
        )
        .all()
    )
    unpaid = [c for c in fines if c.status != ChallanStatus.PAID]

    alert_incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .order_by(RoadIncident.starts_at.desc())
        .limit(5)
        .all()
    )
    alerts = []
    for inc in alert_incidents:
        road_name = None
        if inc.road_id:
            road = db.query(Road).filter(Road.id == inc.road_id).first()
            road_name = road.name if road else None
        alerts.append(
            RouteAlertOut(
                id=inc.id,
                incident_type=inc.incident_type.value,
                severity=inc.severity.value,
                description=inc.description,
                road_name=road_name,
                starts_at=inc.starts_at,
            ).model_dump()
        )

    unread = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read == False)
        .count()
    )

    return PortalDashboard(
        licence=licence,
        vehicles=[
            {
                "id": str(v.id),
                "registration_number": v.registration_number,
                "make": v.make,
                "model": v.model,
                "year": v.year,
                "status": v.status.value,
                "is_blacklisted": v.is_blacklisted,
            }
            for v in vehicles
        ],
        outstanding_fines=[
            FineOut(
                id=c.id,
                reference=c.reference,
                violation_type=(
                    db.query(Violation.violation_type)
                    .filter(Violation.id == c.violation_id)
                    .scalar()
                    or "fine"
                ),
                location=(
                    db.query(Violation.location)
                    .filter(Violation.id == c.violation_id)
                    .scalar()
                    or ""
                ),
                recorded_at=(
                    db.query(Violation.timestamp)
                    .filter(Violation.id == c.violation_id)
                    .scalar()
                    or c.created_at
                ),
                penalty_amount=c.penalty_amount,
                due_date=c.due_date,
                status=c.status,
            )
            for c in unpaid
        ],
        total_outstanding=sum(c.penalty_amount for c in unpaid),
        active_alerts=alerts,
        unread_notifications=unread,
    )


@router.get("/licence", response_model=LicenceStatusOut)
def my_licence(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    driver = _ensure_driver_registered(db, current_user)
    state, days = _licence_state(driver)
    return LicenceStatusOut(
        licence_number=driver.licence_number,
        licence_class=driver.licence_class,
        status=driver.status,
        issue_date=driver.licence_issue_date,
        expiry_date=driver.licence_expiry_date,
        days_until_expiry=days,
        state=state,
        restrictions=driver.restrictions,
    )


@router.post("/licence/renew", response_model=LicenceStatusOut)
def renew_licence(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Renew (or reinstate) a licence and charge the renewal fee via sandbox gateway."""
    driver = _ensure_driver_registered(db, current_user)

    old_expiry = driver.licence_expiry_date
    base = max(old_expiry, datetime.utcnow())
    driver.licence_expiry_date = base + timedelta(days=365 * 5)
    if driver.status in (DriverStatus.EXPIRED, DriverStatus.SUSPENDED):
        driver.status = DriverStatus.ACTIVE

    payment = Payment(
        reference=generate_payment_reference(),
        payment_type=PaymentType.FEE,
        related_entity_id=driver.id,
        amount=LICENCE_RENEWAL_FEE,
        status=PaymentStatus.COMPLETED,
        gateway="sandbox",
        paid_at=datetime.utcnow(),
        paid_by=current_user.id,
    )
    db.add(payment)

    db.flush()
    log_action(
        db, "renew_licence", "driver", str(driver.id),
        f"Renewed {driver.licence_number} until {driver.licence_expiry_date.date()}", current_user.id
    )
    notify(
        db,
        current_user.id,
        "licence_renewed",
        {"licence_number": driver.licence_number, "expiry_date": driver.licence_expiry_date.strftime("%Y-%m-%d")},
    )
    db.commit()
    db.refresh(driver)

    state, days = _licence_state(driver)
    return LicenceStatusOut(
        licence_number=driver.licence_number,
        licence_class=driver.licence_class,
        status=driver.status,
        issue_date=driver.licence_issue_date,
        expiry_date=driver.licence_expiry_date,
        days_until_expiry=days,
        state=state,
        restrictions=driver.restrictions,
    )


@router.get("/fines", response_model=list[FineOut])
def my_fines(
    include_paid: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    vehicles = _get_my_vehicles(db, current_user)
    vehicle_ids = [v.id for v in vehicles]
    query = db.query(Challan).filter(
        Challan.vehicle_id.in_(vehicle_ids) if vehicle_ids
        else Challan.vehicle_id.is_(None)
    )
    if not include_paid:
        query = query.filter(Challan.status != ChallanStatus.PAID)
    challans = query.order_by(Challan.created_at.desc()).all()

    return [
        FineOut(
            id=c.id,
            reference=c.reference,
            violation_type=(
                db.query(Violation.violation_type).filter(Violation.id == c.violation_id).scalar()
                or "fine"
            ),
            location=(
                db.query(Violation.location).filter(Violation.id == c.violation_id).scalar()
                or ""
            ),
            recorded_at=(
                db.query(Violation.timestamp).filter(Violation.id == c.violation_id).scalar()
                or c.created_at
            ),
            penalty_amount=c.penalty_amount,
            due_date=c.due_date,
            status=c.status,
        )
        for c in challans
    ]


@router.post("/fines/{challan_id}/pay", response_model=dict)
def pay_fine(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")

    vehicles = _get_my_vehicles(db, current_user)
    if challan.vehicle_id not in [v.id for v in vehicles]:
        raise HTTPException(status_code=403, detail="Challan does not belong to your vehicles")

    if challan.status == ChallanStatus.PAID:
        raise HTTPException(status_code=400, detail="Challan already paid")

    payment = Payment(
        reference=generate_payment_reference(),
        payment_type=PaymentType.FINE,
        related_entity_id=challan.id,
        amount=challan.penalty_amount,
        status=PaymentStatus.COMPLETED,
        gateway="sandbox",
        paid_at=datetime.utcnow(),
        paid_by=current_user.id,
    )
    db.add(payment)
    challan.status = ChallanStatus.PAID

    db.flush()
    log_action(
        db, "pay", "challan", str(challan.id),
        f"{current_user.full_name} paid {challan.reference}", current_user.id
    )
    notify(
        db,
        current_user.id,
        "payment_receipt",
        {"amount": challan.penalty_amount, "reference": payment.reference},
    )
    db.commit()
    return {
        "challan_reference": challan.reference,
        "payment_reference": payment.reference,
        "status": "paid",
        "message": "Fine settled. Receipt sent to your notifications.",
    }


@router.get("/alerts", response_model=list[RouteAlertOut])
def my_alerts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .order_by(RoadIncident.starts_at.desc())
        .all()
    )
    alerts = []
    for inc in incidents:
        road_name = None
        if inc.road_id:
            road = db.query(Road).filter(Road.id == inc.road_id).first()
            road_name = road.name if road else None
        alerts.append(
            RouteAlertOut(
                id=inc.id,
                incident_type=inc.incident_type.value,
                severity=inc.severity.value,
                description=inc.description,
                road_name=road_name,
                starts_at=inc.starts_at,
            )
        )
    return alerts


@router.post("/login")
def login(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Driver-portal login: accepts email/password and links the user to their licence.
    Simpler variant of /api/auth/login for the portal UI.
    """
    email = payload.get("email")
    password = payload.get("password")
    if not email or not password:
        raise HTTPException(status_code=400, detail="email and password required")
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(data={"sub": str(user.id), "role": user.role.value})
    return {"access_token": token, "token_type": "bearer"}