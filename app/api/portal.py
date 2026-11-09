from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Request, Body, UploadFile
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.services.auth import authenticate
from app.models.driver import Driver, DriverStatus
from app.models.enforcement import Appeal, Challan, ChallanStatus, Violation, ViolationPhoto
from app.models.notification import Notification, NotificationChannel
from app.models.payment import Payment, PaymentStatus, PaymentType
from app.models.road_network import Road, RoadIncident
from app.models.user import User
from app.models.vehicle import Vehicle, VehicleStatus
from app.schemas.portal import (
    FineOut,
    AppealIn,
    AppealOut,
    NominateIn,
    StolenReportIn,
    NominationOut,
    NominationResponseIn,
    LicenceStatusOut,
    PortalDashboard,
    RouteAlertOut,
)
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify
from app.services.appeals import CAN_APPEAL, REASONS, file_appeal
from app.services.demerits import active_points
from app.services.nomination import nominate, respond
from app.services.stolen import mark_recovered, report_stolen
from app.services import fines as fines_service
from app.services.ownership import _linked_drivers, owed_challans_query, owes_challan, owned_vehicles_query
from app.services.payments import create_payment
from app.services.routing import describe_place


def _alert_out(db: Session, inc: RoadIncident) -> RouteAlertOut:
    road_name, stretch = describe_place(db, inc.road_id, inc.segment_id)
    return RouteAlertOut(
        id=inc.id,
        incident_type=inc.incident_type.value,
        severity=inc.severity.value,
        description=inc.description,
        road_name=road_name,
        stretch=stretch,
        starts_at=inc.starts_at,
        verification=inc.verification,
    )

router = APIRouter(prefix="/api/portal", tags=["Driver Portal"])

LICENCE_RENEWAL_FEE = 350000
# A licence can be renewed from this many days before it expires (or once expired).
RENEWAL_WINDOW_DAYS = 90
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


def _licence_out(driver: Driver, db: Session | None = None) -> LicenceStatusOut:
    state, days = _licence_state(driver)
    opens = driver.licence_expiry_date - timedelta(days=RENEWAL_WINDOW_DAYS)
    return LicenceStatusOut(
        licence_number=driver.licence_number,
        licence_class=driver.licence_class,
        status=driver.status,
        issue_date=driver.licence_issue_date,
        expiry_date=driver.licence_expiry_date,
        days_until_expiry=days,
        state=state,
        restrictions=driver.restrictions,
        # Same rules as renew_licence, so the portal only offers what will work.
        can_renew=driver.status not in (DriverStatus.SUSPENDED, DriverStatus.DISQUALIFIED)
        and datetime.utcnow() >= opens,
        renewal_opens=opens,
        demerit_points=active_points(db, driver.id) if db is not None else 0,
        demerit_limit=runtime_settings.get(db, "enforcement.demerit_limit") if db is not None else 12,
    )


def _get_my_licence(db: Session, user: User) -> Driver | None:
    return db.query(Driver).filter(Driver.user_id == user.id).first()


def _get_my_vehicles(db: Session, user: User) -> list[Vehicle]:
    return owned_vehicles_query(db, user).order_by(Vehicle.registration_date.desc()).all()


def _violations_for(db: Session, challans: list[Challan]) -> dict[str, Violation]:
    """The violation behind each challan, keyed by string id."""
    ids = [c.violation_id for c in challans if c.violation_id]
    if not ids:
        return {}
    return {str(v.id): v for v in db.query(Violation).filter(Violation.id.in_(ids)).all()}


def _officer_names(db: Session, violations) -> dict[str, str]:
    """Display names of the officers who recorded these violations, keyed by user id."""
    ids = {v.recorded_by for v in violations if v and v.recorded_by}
    if not ids:
        return {}
    return {str(u.id): u.full_name for u in db.query(User).filter(User.id.in_(ids)).all()}


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
        licence = _licence_out(driver, db)

    vehicles = _get_my_vehicles(db, current_user)
    fines_service.ensure_current(db)
    fines = owed_challans_query(db, current_user).all()
    unpaid = [c for c in fines if c.status in fines_service.OWED]   # same rule as every other screen (appeals are not owed)
    unpaid_violations = _violations_for(db, unpaid)
    unpaid_officers = _officer_names(db, unpaid_violations.values())

    def _dashboard_fine(c: Challan) -> FineOut:
        v = unpaid_violations.get(str(c.violation_id))
        return FineOut(
            id=c.id,
            reference=c.reference,
            violation_type=v.violation_type.value if v else "fine",
            location=v.location if v else "",
            recorded_at=v.timestamp if v else c.created_at,
            officer_name=(unpaid_officers.get(str(v.recorded_by)) if v and v.recorded_by else None),
            penalty_amount=c.penalty_amount,
            due_date=c.due_date,
            status=c.status,
        )

    alert_incidents = (
        db.query(RoadIncident)
        .filter(RoadIncident.is_active == True)
        .order_by(RoadIncident.starts_at.desc())
        .limit(5)
        .all()
    )
    alerts = [_alert_out(db, inc).model_dump() for inc in alert_incidents]

    unread = (
        db.query(Notification)
        # In-app only, like the bell: email and SMS copies of the same event
        # would otherwise count it two or three times.
        .filter(
            Notification.user_id == current_user.id,
            Notification.channel == NotificationChannel.IN_APP,
            Notification.read == False,  # noqa: E712
        )
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
        outstanding_fines=[_dashboard_fine(c) for c in unpaid],
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
    return _licence_out(driver, db)


@router.post("/licence/renew", response_model=LicenceStatusOut)
def renew_licence(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Renew (or reinstate) a licence and charge the renewal fee via sandbox gateway."""
    driver = _ensure_driver_registered(db, current_user)
    if driver.status in (DriverStatus.SUSPENDED, DriverStatus.DISQUALIFIED):
        raise HTTPException(
            status_code=409,
            detail=f"Your licence is {driver.status.value}. Renewing can't lift that: contact RTSA.",
        )
    old_expiry = driver.licence_expiry_date
    opens = old_expiry - timedelta(days=RENEWAL_WINDOW_DAYS)
    if datetime.utcnow() < opens:
        raise HTTPException(
            status_code=409,
            detail=f"Your licence is valid until {old_expiry:%d %b %Y}. Renewal opens on {opens:%d %b %Y}.",
        )

    base = max(old_expiry, datetime.utcnow())
    driver.licence_expiry_date = base + timedelta(days=365 * 5)
    if driver.status == DriverStatus.EXPIRED:
        driver.status = DriverStatus.ACTIVE

    create_payment(
        db, current_user, PaymentType.FEE, driver.id, LICENCE_RENEWAL_FEE,
        description=f"Licence renewal {driver.licence_number}",
    )

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

    return _licence_out(driver, db)


@router.get("/fines", response_model=list[FineOut])
def my_fines(
    include_paid: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fines_service.ensure_current(db)
    query = owed_challans_query(db, current_user)
    if not include_paid:
        query = query.filter(Challan.status.notin_([ChallanStatus.PAID, ChallanStatus.CANCELLED]))
    challans = query.order_by(Challan.created_at.desc()).all()

    violations = _violations_for(db, challans)
    officers = _officer_names(db, violations.values())
    vehicle_ids = {c.vehicle_id for c in challans if c.vehicle_id}
    plates = (
        {str(v.id): v.registration_number for v in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids))}
        if vehicle_ids
        else {}
    )

    appeals = {}
    for a in db.query(Appeal).filter(Appeal.challan_id.in_([c.id for c in challans])).order_by(Appeal.created_at).all() if challans else []:
        appeals[str(a.challan_id)] = a.status  # the latest one wins

    def _fine(c: Challan) -> FineOut:
        # Keys are strings; look up by str(c.violation_id), not the UUID itself.
        v = violations.get(str(c.violation_id))
        return FineOut(
            id=c.id,
            reference=c.reference,
            violation_type=v.violation_type.value if v else "fine",
            category=v.violation_type.category if v else "both",
            liable_party=v.liable_party if v else "both",
            location=v.location if v else "",
            recorded_at=v.timestamp if v else c.created_at,
            officer_name=(officers.get(str(v.recorded_by)) if v and v.recorded_by else None),
            registration_number=plates.get(str(c.vehicle_id)) if c.vehicle_id else None,
            penalty_amount=c.penalty_amount,
            due_date=c.due_date,
            status=c.status,
            nomination_status=c.nomination_status,
            nomination_deadline=c.nomination_deadline,
            appeal_status=appeals.get(str(c.id)),
            can_appeal=c.status in CAN_APPEAL and appeals.get(str(c.id)) in (None, "upheld", "withdrawn"),
        )

    return [_fine(c) for c in challans]


def _nomination_out(db: Session, c: Challan, role: str) -> NominationOut:
    v = db.get(Violation, c.violation_id)
    plate = db.get(Vehicle, c.vehicle_id).registration_number if c.vehicle_id else None
    named_by = db.get(User, c.nominated_by).full_name if role == "driver" and c.nominated_by else None
    return NominationOut(
        id=c.id, reference=c.reference, role=role, status=c.nomination_status,
        registration_number=plate,
        violation_type=v.violation_type.value if v else "offence",
        location=v.location if v else "",
        occurred_at=v.timestamp if v else c.created_at,
        penalty_amount=c.penalty_amount, deadline=c.nomination_deadline,
        named_by=named_by, statement=c.nomination_statement if role == "driver" else None,
    )


@router.get("/nominations", response_model=list[NominationOut])
def my_nominations(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """What needs answering: fines on my vehicles that ask who was driving, and fines naming me."""
    now = datetime.utcnow()
    vehicle_ids = owned_vehicles_query(db, current_user).with_entities(Vehicle.id)
    asked = (
        db.query(Challan)
        .filter(Challan.nomination_status == "requested", Challan.vehicle_id.in_(vehicle_ids),
                Challan.nomination_deadline >= now, Challan.status != ChallanStatus.PAID)
        .order_by(Challan.created_at.desc()).all()
    )
    driver_ids = [d.id for d in _linked_drivers(db, current_user).with_entities(Driver.id)]
    named = (
        db.query(Challan)
        .filter(Challan.nomination_status == "nominated", Challan.nominated_driver_id.in_(driver_ids))
        .order_by(Challan.created_at.desc()).all()
        if driver_ids else []
    )
    return [_nomination_out(db, c, "owner") for c in asked] + [_nomination_out(db, c, "driver") for c in named]


@router.post("/fines/{challan_id}/nominate", response_model=NominationOut)
def nominate_driver(
    challan_id: str, payload: NominateIn,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The owner names who was driving, by licence number."""
    c = nominate(db, current_user, challan_id, payload.licence_number, payload.statement)
    db.commit()
    return _nomination_out(db, c, "owner")


@router.post("/nominations/{challan_id}/respond", response_model=NominationOut)
def respond_to_nomination(
    challan_id: str, payload: NominationResponseIn,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The named driver accepts the fine or says it wasn't them."""
    c = respond(db, current_user, challan_id, payload.accept, payload.note)
    db.commit()
    return _nomination_out(db, c, "driver")


@router.post("/fines/{challan_id}/pay", response_model=dict)
def pay_fine(
    challan_id: str,
    payload: dict | None = Body(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")

    if not owes_challan(db, current_user, challan):
        raise HTTPException(status_code=403, detail="This fine is not charged to you or your vehicles")

    payload = payload or {}
    method = payload.get("method")
    gateway = {"mobile_money": "sim_mobile", "card": "sim_card"}.get(method, "sandbox")
    payment, _ = create_payment(db, current_user, PaymentType.FINE, challan.id, gateway=gateway, sim=payload.get("details"))
    db.commit()
    ok = payment.status.value == "completed"
    return {
        "challan_reference": challan.reference,
        "payment_reference": payment.reference,
        "receipt_number": payment.receipt_number,
        "status": "paid" if ok else "failed",
        "reason": payment.failure_reason,
        "message": "Fine settled. Receipt sent to your notifications." if ok else (payment.failure_reason or "Payment failed."),
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
    return [_alert_out(db, inc) for inc in incidents]


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
    return authenticate(db, request, email, password)


@router.post("/fines/{challan_id}/appeal", response_model=AppealOut, status_code=201)
def appeal_fine(
    challan_id: str, payload: AppealIn,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """Contest a fine. It is marked disputed (no late surcharge) until an officer decides."""
    if payload.reason not in REASONS:
        raise HTTPException(status_code=422, detail="Choose one of: " + ", ".join(REASONS))
    a = file_appeal(db, current_user, challan_id, payload.reason, payload.details)
    db.commit()
    return AppealOut.model_validate(a)


@router.get("/appeals", response_model=list[AppealOut])
def my_appeals(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    rows = db.query(Appeal).filter(Appeal.appellant_id == current_user.id).order_by(Appeal.created_at.desc()).all()
    return [AppealOut.model_validate(a) for a in rows]


MAX_APPEAL_PHOTOS = 5


@router.post("/appeals/{appeal_id}/photos", status_code=201)
def add_appeal_photo(
    appeal_id: str, file: UploadFile = File(...),
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """Attach a photo to your own pending appeal (a receipt, the scene, a screenshot). Added, never replaced."""
    from sqlalchemy import func

    from app.services.evidence import new_photo, read_photo

    a = db.get(Appeal, appeal_id)
    if a is None or a.appellant_id != current_user.id:
        raise HTTPException(status_code=404, detail="Appeal not found")
    if a.status != "pending":
        raise HTTPException(status_code=409, detail="This appeal has already been decided")
    held = db.query(func.count(ViolationPhoto.id)).filter(ViolationPhoto.appeal_id == a.id).scalar()
    if held >= MAX_APPEAL_PHOTOS:
        raise HTTPException(status_code=409, detail=f"An appeal can have up to {MAX_APPEAL_PHOTOS} photos")
    data, content_type = read_photo(file)
    photo = new_photo(data, content_type, current_user.id, appeal_id=a.id)
    db.add(photo)
    db.flush()
    log_action(db, "appeal_photo", "appeal", str(a.id), f"Photo {photo.sha256[:16]} ({len(data)} bytes)", current_user.id)
    db.commit()
    return {"id": str(photo.id), "size_bytes": photo.size_bytes}


@router.get("/appeals/{appeal_id}/photos/{photo_id}")
def appeal_photo(
    appeal_id: str, photo_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """An appeal's photo, for the citizen who filed it or an officer."""
    from app.services.evidence import photo_response

    a = db.get(Appeal, appeal_id)
    if a is None or (a.appellant_id != current_user.id and current_user.role.value not in ("officer", "admin")):
        raise HTTPException(status_code=404, detail="Photo not found")
    photo = db.query(ViolationPhoto).filter(ViolationPhoto.id == photo_id, ViolationPhoto.appeal_id == a.id).first()
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return photo_response(photo)


@router.post("/vehicles/{vehicle_id}/report-stolen", response_model=dict)
def report_vehicle_stolen(
    vehicle_id: str, payload: StolenReportIn,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The owner reports a theft. Fines for offences after it are waived; sightings go to officers."""
    v = report_stolen(db, current_user, vehicle_id, payload.police_report_number, payload.stolen_at)
    db.commit()
    return {"registration_number": v.registration_number, "status": "stolen", "police_report_number": v.police_report_number, "fines_waived": getattr(v, "_waived", 0)}


@router.post("/vehicles/{vehicle_id}/recovered", response_model=dict)
def mark_vehicle_recovered(
    vehicle_id: str,
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
):
    """The owner has their vehicle back, or reported it stolen by mistake: undo it.

    Fines are left alone. Only offences recorded after the theft had been waived, and those stay
    cancelled: a wrong theft report is corrected by an officer, not by inventing charges.
    """
    vehicle = owned_vehicles_query(db, current_user).filter(Vehicle.id == vehicle_id).first()
    if vehicle is None or vehicle.status != VehicleStatus.STOLEN:
        raise HTTPException(status_code=404, detail="No stolen vehicle with that id")
    v = mark_recovered(db, current_user, vehicle_id)
    db.commit()
    return {"registration_number": v.registration_number, "status": v.status.value}
