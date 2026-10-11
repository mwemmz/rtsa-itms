import hashlib
import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import OFFICERS, get_current_user, require_role
from app.models.driver import Driver
from app.models.enforcement import (
    REPORTER_OFFENCES,
    Challan,
    ChallanStatus,
    Violation,
    ViolationPhoto,
    ViolationType,
)
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.enforcement import (
    ChallanBrief,
    ChallanPaymentResult,
    ChallanResponse,
    IdentifyDriver,
    TicketSms,
    ViolationCreate,
    ViolationPhotoResponse,
    ViolationResponse,
)
from app.models.payment import PaymentType
from app.services import exporters
from app.services.audit import log_action
from app.services.delivery import send_sms
from app.services.ownership import owner_user_id
from app.services.payments import create_payment
from app.services.notifications import notify

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/enforcement", tags=["Enforcement"])

# Zambia keeps Central Africa Time (UTC+2) all year, so a fixed offset is exact.
CAT = timezone(timedelta(hours=2), "CAT")

EVIDENCE_LABELS = {
    "citizen_video": "Citizen's video",
    "social_media": "Social media post",
    "cctv": "CCTV / traffic camera",
}

VIOLATION_PENALTIES = {
    # Driver offences: fine, and the licence is the real consequence.
    ViolationType.SPEEDING: 500000,
    ViolationType.RUNNING_RED_LIGHT: 300000,
    ViolationType.DRUNK_DRIVING: 2000000,
    ViolationType.RECKLESS_DRIVING: 1500000,
    ViolationType.USING_PHONE: 200000,
    ViolationType.REAR_SEAT_BELT: 100000,
    ViolationType.DRIVING_WITHOUT_LICENCE: 600000,
    # Vehicle offences: fine, and the vehicle is the real consequence.
    ViolationType.EXPIRED_FITNESS: 800000,
    ViolationType.UNROADWORTHY: 1000000,
    ViolationType.EXPIRED_ROAD_TAX: 500000,
    ViolationType.MISSING_NUMBER_PLATES: 300000,
    ViolationType.ILLEGAL_MODIFICATION: 900000,
    ViolationType.OVERLOADING: 400000,
    ViolationType.NO_PSV_PERMIT: 1500000,
    ViolationType.BLACKLISTED_VEHICLE: 2000000,
    # Overlap: both driver and owner can be liable.
    ViolationType.NO_INSURANCE: 1000000,
    ViolationType.ILLEGAL_PARKING: 150000,
    ViolationType.OTHER: 100000,
}


def generate_challan_reference() -> str:
    return f"CH-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"


def _enrich_violations(db: Session, violations: list[Violation]) -> list[dict]:
    """Attach offender identity (vehicle plate + owner, driver name) to violations."""
    vehicle_ids = list({v.vehicle_id for v in violations if v.vehicle_id})
    driver_ids = list({v.driver_id for v in violations if v.driver_id})
    vehicles = (
        {str(x.id): x for x in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all()}
        if vehicle_ids
        else {}
    )
    drivers = (
        {str(x.id): x for x in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
        if driver_ids
        else {}
    )
    accounts = _account_names(db, [v.user_id for v in violations])
    photo_counts = (
        {
            str(vid): n
            for vid, n in db.query(ViolationPhoto.violation_id, func.count(ViolationPhoto.id))
            .filter(ViolationPhoto.violation_id.in_([v.id for v in violations]))
            .group_by(ViolationPhoto.violation_id)
        }
        if violations
        else {}
    )
    challans = (
        {
            str(c.violation_id): ChallanBrief.model_validate(c)
            for c in db.query(Challan).filter(Challan.violation_id.in_([v.id for v in violations]))
        }
        if violations
        else {}
    )

    out = []
    for v in violations:
        veh = vehicles.get(str(v.vehicle_id)) if v.vehicle_id else None
        drv = drivers.get(str(v.driver_id)) if v.driver_id else None
        out.append(
            {
                "id": v.id,
                "vehicle_id": v.vehicle_id,
                "driver_id": v.driver_id,
                "violation_type": v.violation_type,
                "category": v.violation_type.category,
                "liable_party": v.liable_party,
                "carries_licence_consequence": v.violation_type.carries_licence_consequence,
                "grounds_impoundment": v.violation_type.grounds_impoundment,
                "location": v.location,
                "speed_kmh": v.speed_kmh,
                "speed_limit_kmh": v.speed_limit_kmh,
                "speed_device": v.speed_device,
                "latitude": v.latitude,
                "longitude": v.longitude,
                "gps_accuracy_m": v.gps_accuracy_m,
                "evidence_source": v.evidence_source,
                "evidence_url": v.evidence_url,
                "timestamp": v.timestamp,
                "description": v.description,
                "registration_number": veh.registration_number if veh else None,
                "owner_name": veh.owner_name if veh else None,
                "owner_id_number": veh.owner_id_number if veh else None,
                "driver_name": f"{drv.first_name} {drv.last_name}".strip() if drv else None,
                "account_name": accounts.get(str(v.user_id)) if v.user_id else None,
                "challan": challans.get(str(v.id)),
                "photo_count": photo_counts.get(str(v.id), 0),
            }
        )
    return out


def _account_names(db: Session, user_ids: list) -> dict[str, str]:
    """Name (and NRC) of the accounts charged directly, e.g. for a false report."""
    ids = list({u for u in user_ids if u})
    if not ids:
        return {}
    return {
        str(u.id): u.full_name + (f" (NRC {u.nrc_number})" if u.nrc_number else "")
        for u in db.query(User).filter(User.id.in_(ids))
    }


def _enrich_challans(db: Session, challans: list[Challan]) -> list[dict]:
    """Attach offender identity to challans so dashboards identify who is charged."""
    vehicle_ids = list({c.vehicle_id for c in challans if c.vehicle_id})
    driver_ids = list({c.driver_id for c in challans if c.driver_id})
    vehicles = (
        {str(x.id): x for x in db.query(Vehicle).filter(Vehicle.id.in_(vehicle_ids)).all()}
        if vehicle_ids
        else {}
    )
    drivers = (
        {str(x.id): x for x in db.query(Driver).filter(Driver.id.in_(driver_ids)).all()}
        if driver_ids
        else {}
    )
    accounts = _account_names(db, [c.user_id for c in challans])
    violation_ids = list({c.violation_id for c in challans if c.violation_id})
    violations = (
        {str(x.id): x for x in db.query(Violation).filter(Violation.id.in_(violation_ids)).all()}
        if violation_ids
        else {}
    )

    out = []
    for c in challans:
        veh = vehicles.get(str(c.vehicle_id)) if c.vehicle_id else None
        drv = drivers.get(str(c.driver_id)) if c.driver_id else None
        vio = violations.get(str(c.violation_id)) if c.violation_id else None
        out.append(
            {
                "id": c.id,
                "reference": c.reference,
                "violation_id": c.violation_id,
                "violation_type": vio.violation_type if vio else None,
                "category": vio.violation_type.category if vio else "both",
                "liable_party": vio.liable_party if vio else "both",
                "carries_licence_consequence": bool(vio and vio.violation_type.carries_licence_consequence),
                "grounds_impoundment": bool(vio and vio.violation_type.grounds_impoundment),
                "vehicle_id": c.vehicle_id,
                "driver_id": c.driver_id,
                "penalty_amount": c.penalty_amount,
                "due_date": c.due_date,
                "status": c.status,
                "created_at": c.created_at,
                "registration_number": veh.registration_number if veh else None,
                "owner_name": veh.owner_name if veh else None,
                "driver_name": f"{drv.first_name} {drv.last_name}".strip() if drv else None,
                "account_name": accounts.get(str(c.user_id)) if c.user_id else None,
            }
        )
    return out


def create_challan_for_violation(
    db: Session,
    violation: Violation,
    actor_id: uuid.UUID | None,
    penalty_amount: int | None = None,
    due_days: int = 14,
) -> Challan:
    challan = Challan(
        reference=generate_challan_reference(),
        violation_id=violation.id,
        vehicle_id=violation.vehicle_id,
        driver_id=violation.driver_id,
        user_id=violation.user_id,
        penalty_amount=(
            penalty_amount if penalty_amount is not None
            else VIOLATION_PENALTIES.get(violation.violation_type, 100000)
        ),
        due_date=datetime.utcnow() + timedelta(days=due_days),
    )
    db.add(challan)
    db.flush()
    log_action(
        db, "generate_challan", "challan", str(challan.id),
        f"Generated {challan.reference} for {violation.violation_type.value}", actor_id
    )
    return challan


@router.post("/violations", response_model=ViolationResponse, status_code=status.HTTP_201_CREATED)
def record_violation(
    payload: ViolationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    if payload.violation_type in REPORTER_OFFENCES:
        raise HTTPException(
            status_code=422,
            detail="False-report fines are issued by rejecting the report on the Road alerts screen",
        )
    violation = Violation(
        vehicle_id=payload.vehicle_id,
        driver_id=payload.driver_id,
        violation_type=payload.violation_type,
        location=payload.location,
        speed_kmh=payload.speed_kmh,
        speed_limit_kmh=payload.speed_limit_kmh,
        speed_device=payload.speed_device,
        latitude=payload.latitude,
        longitude=payload.longitude,
        gps_accuracy_m=payload.gps_accuracy_m,
        evidence_source=payload.evidence_source,
        evidence_url=payload.evidence_url,
        timestamp=payload.timestamp or datetime.utcnow(),
        description=payload.description,
        recorded_by=current_user.id,
        liable_party=payload.violation_type.liable_party,
    )
    db.add(violation)
    db.flush()

    challan = create_challan_for_violation(db, violation, current_user.id)
    db.flush()

    # The vehicle owner hears about every fine on their vehicle; the driver
    # about the ones they are liable for, since those carry licence points.
    vehicle = db.query(Vehicle).filter(Vehicle.id == violation.vehicle_id).first()
    owner_id = owner_user_id(db, vehicle)
    if owner_id:
        _notify_challan(db, owner_id, challan)
    driver_id = _liable_driver_user_id(db, violation)
    if driver_id and driver_id != owner_id:
        _notify_challan(db, driver_id, challan)

    db.commit()
    return _enrich_violations(db, [violation])[0]


def _notify_challan(db: Session, user_id: uuid.UUID, challan: Challan) -> None:
    notify(
        db,
        user_id,
        "challan_created",
        {
            # Formatted here: the template is admin-managed and just drops these in.
            "reference": challan.reference,
            "amount": f"{challan.penalty_amount:,} ZMW",
            "due_date": challan.due_date.strftime("%d %b %Y"),
        },
    )


def _liable_driver_user_id(db: Session, violation: Violation) -> uuid.UUID | None:
    """The account of the driver, when the offence is one the driver answers for."""
    if violation.driver_id is None or violation.liable_party not in ("driver", "both"):
        return None
    driver = db.get(Driver, violation.driver_id)
    return driver.user_id if driver else None


@router.get("/violations", response_model=list[ViolationResponse])
def list_violations(
    vehicle_id: str | None = Query(None),
    mine: bool = Query(False, description="Only violations the caller recorded, newest recording first"),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    query = db.query(Violation)
    if vehicle_id:
        query = query.filter(Violation.vehicle_id == vehicle_id)
    if mine:
        # By when it was recorded: a video case from last week recorded this
        # morning is part of this shift's work.
        query = query.filter(Violation.recorded_by == current_user.id).order_by(Violation.created_at.desc())
    else:
        query = query.order_by(Violation.timestamp.desc())
    violations = query.offset(skip).limit(limit).all()
    return _enrich_violations(db, violations)


@router.post("/violations/{violation_id}/driver", response_model=ViolationResponse)
def identify_driver(
    violation_id: str,
    payload: IdentifyDriver,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Attach the driver to a violation recorded against a vehicle alone.

    An offence seen on camera or video usually shows the plate, not who was
    driving. The owner names the driver; the officer records it here, and the
    driver is charged and notified from then on.
    """
    violation = db.query(Violation).filter(Violation.id == violation_id).first()
    if violation is None:
        raise HTTPException(status_code=404, detail="Violation not found")
    if violation.violation_type in REPORTER_OFFENCES:
        raise HTTPException(status_code=422, detail="A false-report fine is charged to the reporter, not a driver")
    if violation.driver_id is not None:
        raise HTTPException(status_code=409, detail="This violation already has a driver")
    driver = db.query(Driver).filter(Driver.licence_number == payload.licence_number.strip()).first()
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver holds that licence number")

    violation.driver_id = driver.id
    challans = db.query(Challan).filter(Challan.violation_id == violation.id).all()
    for challan in challans:
        if challan.driver_id is None:
            challan.driver_id = driver.id
    log_action(
        db, "identify_driver", "violation", str(violation.id),
        f"Driver {driver.licence_number} identified for {violation.violation_type.value}", current_user.id
    )
    driver_user = _liable_driver_user_id(db, violation)
    owner = owner_user_id(db, db.get(Vehicle, violation.vehicle_id)) if violation.vehicle_id else None
    if driver_user and driver_user != owner:  # the owner was told when it was recorded
        for challan in challans:
            _notify_challan(db, driver_user, challan)
    db.commit()
    return _enrich_violations(db, [violation])[0]


# ------------------------------- photo evidence -------------------------------

MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTOS_PER_VIOLATION = 5
PHOTO_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


def _image_type(data: bytes) -> str | None:
    """The image type from the file's first bytes, never its name or declared
    type, so nothing but a photo is stored or served back."""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _violation_or_404(db: Session, violation_id: uuid.UUID) -> Violation:
    violation = db.get(Violation, violation_id)
    if violation is None:
        raise HTTPException(status_code=404, detail="Violation not found")
    return violation


def _photo_out(photo: ViolationPhoto, uploader: str | None) -> dict:
    return {
        "id": photo.id,
        "violation_id": photo.violation_id,
        "content_type": photo.content_type,
        "size_bytes": photo.size_bytes,
        "sha256": photo.sha256,
        "uploaded_by_name": uploader,
        "created_at": photo.created_at,
    }


@router.post("/violations/{violation_id}/photos", response_model=ViolationPhotoResponse,
             status_code=status.HTTP_201_CREATED)
def add_violation_photo(
    violation_id: uuid.UUID,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Attach one photo. Photos can be added, never replaced or removed: they are evidence."""
    violation = _violation_or_404(db, violation_id)
    held = db.query(func.count(ViolationPhoto.id)).filter(ViolationPhoto.violation_id == violation.id).scalar()
    if held >= MAX_PHOTOS_PER_VIOLATION:
        raise HTTPException(status_code=409, detail=f"A violation can have up to {MAX_PHOTOS_PER_VIOLATION} photos")
    data = file.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(status_code=413, detail="A photo can be up to 5 MB")
    content_type = _image_type(data)
    if content_type is None:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG or WebP photos can be attached")

    photo = ViolationPhoto(
        violation_id=violation.id,
        content_type=content_type,
        size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        uploaded_by=current_user.id,
    )
    db.add(photo)
    db.flush()
    log_action(db, "add_photo", "violation", str(violation.id),
               f"Photo {photo.sha256[:16]} ({len(data)} bytes)", current_user.id)
    db.commit()
    db.refresh(photo)
    return _photo_out(photo, current_user.full_name)


@router.get("/violations/{violation_id}/photos", response_model=list[ViolationPhotoResponse])
def list_violation_photos(
    violation_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    violation = _violation_or_404(db, violation_id)
    photos = (
        db.query(ViolationPhoto)
        .filter(ViolationPhoto.violation_id == violation.id)
        .order_by(ViolationPhoto.created_at)
        .all()
    )
    uploader_ids = {p.uploaded_by for p in photos if p.uploaded_by}
    names = (
        {str(u.id): u.full_name for u in db.query(User).filter(User.id.in_(uploader_ids))}
        if uploader_ids
        else {}
    )
    return [_photo_out(p, names.get(str(p.uploaded_by))) for p in photos]


@router.get("/violations/{violation_id}/photos/{photo_id}")
def get_violation_photo(
    violation_id: uuid.UUID,
    photo_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    photo = (
        db.query(ViolationPhoto)
        .filter(ViolationPhoto.id == photo_id, ViolationPhoto.violation_id == violation_id)
        .first()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return Response(
        photo.data,
        media_type=photo.content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'inline; filename="evidence-{photo.id}.{PHOTO_EXTENSIONS[photo.content_type]}"',
        },
    )


@router.get("/violations/summary")
def violations_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Violations recorded in the last 24 hours, in total and by the caller.

    The dashboard used to count the rows of a 6-item list, so it never showed
    more than 6. This counts in the database instead.
    """
    since = datetime.utcnow() - timedelta(hours=24)
    recent = db.query(func.count(Violation.id)).filter(Violation.created_at >= since)
    return {
        "last_24h": recent.scalar(),
        "last_24h_by_me": recent.filter(Violation.recorded_by == current_user.id).scalar(),
    }


@router.get("/challans", response_model=list[ChallanResponse])
def list_challans(
    vehicle_id: str | None = Query(None),
    status_filter: ChallanStatus | None = Query(None, alias="status"),
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    query = db.query(Challan)
    if vehicle_id:
        query = query.filter(Challan.vehicle_id == vehicle_id)
    if status_filter:
        query = query.filter(Challan.status == status_filter)
    challans = query.order_by(Challan.created_at.desc()).offset(skip).limit(limit).all()
    return _enrich_challans(db, challans)


@router.get("/challans/{challan_id}", response_model=ChallanResponse)
def get_challan(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")
    violation = db.query(Violation).filter(Violation.id == challan.violation_id).first()
    return _enrich_challans(db, [challan])[0] if violation else challan


# ----------------------------- the motorist's ticket -----------------------------

LIABLE_LABELS = {"driver": "Driver", "owner": "Registered owner", "both": "Driver and registered owner"}


def _cat(value: datetime) -> str:
    """A stored (UTC) time as the motorist reads it: Zambian local time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(CAT).strftime("%d %b %Y, %H:%M CAT")


def _offence_label(violation: Violation) -> str:
    return violation.violation_type.value.replace("_", " ").capitalize()


def _ticket(db: Session, challan_id: str) -> tuple[Challan, Violation, list[tuple[str, str]]]:
    """The challan, its violation, and the ticket's lines in reading order."""
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if challan is None:
        raise HTTPException(status_code=404, detail="Challan not found")
    violation = db.get(Violation, challan.violation_id)
    if violation is None or violation.violation_type in REPORTER_OFFENCES:
        raise HTTPException(status_code=422, detail="Only road offences have a roadside ticket")
    vehicle = db.get(Vehicle, challan.vehicle_id) if challan.vehicle_id else None
    driver = db.get(Driver, challan.driver_id) if challan.driver_id else None
    officer = db.get(User, violation.recorded_by) if violation.recorded_by else None

    lines = [("Reference", challan.reference), ("Offence", _offence_label(violation))]
    if violation.violation_type.carries_licence_consequence:
        lines.append(("Consequence", "Counts towards licence points, endorsement or disqualification"))
    elif violation.violation_type.grounds_impoundment:
        lines.append(("Consequence", "The vehicle can be impounded or taken off the road"))
    lines.append(("Liable", LIABLE_LABELS.get(violation.liable_party, violation.liable_party)))
    if vehicle:
        lines.append(("Vehicle", f"{vehicle.registration_number} ({vehicle.make} {vehicle.model})"))
        lines.append(("Registered owner", vehicle.owner_name))
    if driver:
        lines.append(("Driver", f"{driver.first_name} {driver.last_name}, licence {driver.licence_number}"))
    elif violation.liable_party in ("driver", "both"):
        lines.append(("Driver", "Not identified: the registered owner must name the driver"))
    place = violation.location
    # The form fills an empty location with "GPS lat, lng"; don't say it twice.
    if violation.latitude is not None and violation.longitude is not None and not place.startswith("GPS "):
        place += f" (GPS {violation.latitude:.5f}, {violation.longitude:.5f})"
    lines += [("Where", place), ("When", _cat(violation.timestamp))]
    if violation.speed_kmh is not None:
        speed = f"{violation.speed_kmh:g} km/h"
        if violation.speed_limit_kmh is not None:
            speed += f" where the limit is {violation.speed_limit_kmh:g} km/h"
        if violation.speed_device:
            speed += f", measured by {violation.speed_device}"
        lines.append(("Speed", speed))
    if violation.evidence_source:
        evidence = EVIDENCE_LABELS.get(violation.evidence_source, violation.evidence_source)
        lines.append(("Evidence", evidence + (f": {violation.evidence_url}" if violation.evidence_url else "")))
    photos = db.query(func.count(ViolationPhoto.id)).filter(ViolationPhoto.violation_id == violation.id).scalar()
    if photos:
        lines.append(("Photos", f"{photos} on file with RTSA"))
    lines += [
        ("Fine", f"{challan.penalty_amount:,} ZMW"),
        ("Pay by", _cat(challan.due_date).split(",")[0]),
        ("Status", challan.status.value),
        ("Issued by", officer.full_name if officer else "RTSA"),
        ("How to pay", f"Online under Fines & payments in the RTSA portal, quoting {challan.reference}, "
                       "or at any RTSA office."),
    ]
    return challan, violation, lines


@router.get("/challans/{challan_id}/ticket.pdf")
def challan_ticket_pdf(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """The ticket to print and hand to the motorist at the roadside."""
    challan, _, lines = _ticket(db, challan_id)
    return Response(
        exporters.ticket_pdf(challan.reference, lines),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="ticket-{challan.reference}.pdf"'},
    )


@router.post("/challans/{challan_id}/sms")
def send_challan_sms(
    challan_id: str,
    payload: TicketSms,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Text the ticket to the motorist's phone, for when there's no printer.

    The number goes straight to the SMS gateway: the motorist may have no
    account, so this doesn't go through the notification rules.
    """
    challan, violation, lines = _ticket(db, challan_id)
    details = dict(lines)
    plate = f" by {details['Vehicle'].split(' (')[0]}" if "Vehicle" in details else ""
    where = violation.location if len(violation.location) <= 60 else violation.location[:57] + "..."
    text = (
        f"RTSA e-Challan {challan.reference}: {details['Offence']}{plate} at {where}, {details['When']}. "
        f"Fine {details['Fine']}, pay by {details['Pay by']} in the RTSA portal (Fines & payments) "
        f"quoting {challan.reference}."
    )
    masked = "*" * (len(payload.phone_number) - 3) + payload.phone_number[-3:]
    try:
        send_sms(payload.phone_number, text)
    except Exception:
        logger.exception("Ticket SMS for %s failed", challan.reference)
        raise HTTPException(status_code=502, detail="The SMS could not be sent. Print the ticket instead.")
    log_action(db, "send_ticket_sms", "challan", str(challan.id), f"{challan.reference} to {masked}", current_user.id)
    db.commit()
    return {"sent": True, "to": masked}


@router.post("/challans/{challan_id}/pay", response_model=ChallanPaymentResult)
def pay_challan(
    challan_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    challan = db.query(Challan).filter(Challan.id == challan_id).first()
    if not challan:
        raise HTTPException(status_code=404, detail="Challan not found")

    # Settlement (ownership check, ledger, receipt, notification) lives in the payments service.
    create_payment(db, current_user, PaymentType.FINE, challan.id)
    db.commit()
    db.refresh(challan)
    return ChallanPaymentResult(
        challan_id=challan.id,
        reference=challan.reference,
        status=challan.status,
        message="Payment successful",
    )
