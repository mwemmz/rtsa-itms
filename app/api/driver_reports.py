"""Citizens report dangerous driving; officers review it and fine it, or don't.

The citizen sends the plate, what they saw, where and when, and evidence: a
link to a video, photos, or both. Nothing happens to the reported vehicle until
an officer has looked at that evidence and accepted the report, which raises
the violation and e-Challan exactly as the officer's own form would, with the
citizen's photos moved onto it. A report found false fines the reporter, under
the same NRC-backed rules as road reports (app/services/road_reports.py).
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.enforcement import record_violation_for
from app.core.database import get_db
from app.core.security import OFFICERS, get_current_user, require_role
from app.core.timeutil import utcnow
from app.models.driver_report import DriverReport, DriverReportStatus
from app.models.enforcement import Challan, ViolationPhoto, ViolationType
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.driver_report import DriverReportAccept, DriverReportCreate, DriverReportOut, DriverReportReject
from app.schemas.enforcement import ViolationCreate
from app.services.audit import log_action
from app.services.evidence import MAX_PHOTOS_PER_VIOLATION, new_photo, photo_response, read_photo
from app.services.notifications import broadcast, notify
from app.services.plates import find_vehicle_by_plate
from app.services.road_reports import false_report_notice, fine_false_report, reporter_standing

router = APIRouter(prefix="/api/driver-reports", tags=["Driver reports"])


def _offence(value: str) -> str:
    return value.replace("_", " ").capitalize()


def _out(db: Session, report: DriverReport, for_officer: bool) -> dict:
    photos = (
        db.query(ViolationPhoto.id, ViolationPhoto.size_bytes, ViolationPhoto.sha256, ViolationPhoto.created_at)
        .filter(ViolationPhoto.report_id == report.id)
        .order_by(ViolationPhoto.created_at)
        .all()
    )
    out = {
        "id": report.id, "reference": report.reference, "plate_number": report.plate_number,
        "violation_type": report.violation_type, "location": report.location,
        "latitude": report.latitude, "longitude": report.longitude, "gps_accuracy_m": report.gps_accuracy_m,
        "observed_at": report.observed_at, "description": report.description, "evidence_url": report.evidence_url,
        "status": report.status, "review_note": report.review_note, "reviewed_at": report.reviewed_at,
        "created_at": report.created_at,
        "photos": [{"id": p.id, "size_bytes": p.size_bytes, "sha256": p.sha256, "created_at": p.created_at} for p in photos],
    }
    if report.violation_id:
        challan = db.query(Challan).filter(Challan.violation_id == report.violation_id).first()
        out["challan_reference"] = challan.reference if challan else None
    if for_officer:
        reporter = db.get(User, report.reporter_id)
        out["reporter_name"] = reporter.full_name if reporter else None
        out["reporter_nrc"] = reporter.nrc_number if reporter else None
        # How reliable this reporter has been: their false reports in the strike window.
        out["reporter_false_reports"] = reporter_standing(db, reporter).false_reports if reporter else None
        vehicle = db.get(Vehicle, report.vehicle_id) if report.vehicle_id else None
        out["vehicle"] = (
            {"id": str(vehicle.id), "registration_number": vehicle.registration_number,
             "description": f"{vehicle.year} {vehicle.make} {vehicle.model}", "owner": vehicle.owner_name}
            if vehicle else None
        )
    return out


def _pending(db: Session, report_id: uuid.UUID) -> DriverReport:
    report = db.get(DriverReport, report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if report.status != DriverReportStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="This report has already been reviewed")
    return report


@router.post("/", response_model=DriverReportOut, status_code=status.HTTP_201_CREATED)
def report_driver(
    plate_number: str = Form(...),
    violation_type: str = Form(...),
    location: str = Form(...),
    observed_at: str = Form(...),
    description: str = Form(...),
    declaration: bool = Form(False),
    evidence_url: str | None = Form(None),
    latitude: float | None = Form(None),
    longitude: float | None = Form(None),
    gps_accuracy_m: float | None = Form(None),
    photos: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A citizen reports a driver. Evidence is required: a video link, photos, or both."""
    # FastAPI 0.104 can't take a model from a form, so the fields are checked here
    # with the same model a JSON body would use, and fail with the same 422.
    try:
        payload = DriverReportCreate(
            plate_number=plate_number, violation_type=violation_type, location=location,
            observed_at=observed_at, description=description, declaration=declaration,
            evidence_url=evidence_url, latitude=latitude, longitude=longitude, gps_accuracy_m=gps_accuracy_m,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors())

    if current_user.role != UserRole.CITIZEN:
        raise HTTPException(status_code=403, detail="Staff record violations directly instead of reporting them")
    standing = reporter_standing(db, current_user)
    if not standing.can_report:
        raise HTTPException(status_code=403, detail=standing.reason)
    photos = [p for p in photos if p.filename]
    if len(photos) > MAX_PHOTOS_PER_VIOLATION:
        raise HTTPException(status_code=422, detail=f"Send up to {MAX_PHOTOS_PER_VIOLATION} photos")
    if not photos and not payload.evidence_url:
        raise HTTPException(status_code=422, detail="Add a link to a video, at least one photo, or both")
    images = [read_photo(p) for p in photos]  # all checked before anything is saved

    vehicle = find_vehicle_by_plate(db, payload.plate_number)
    report = DriverReport(
        reporter_id=current_user.id,
        plate_number=vehicle.registration_number if vehicle else payload.plate_number,
        vehicle_id=vehicle.id if vehicle else None,
        violation_type=payload.violation_type.value,
        location=payload.location,
        latitude=payload.latitude, longitude=payload.longitude,
        gps_accuracy_m=payload.gps_accuracy_m if payload.latitude is not None else None,
        observed_at=payload.observed_at,
        description=payload.description,
        evidence_url=payload.evidence_url,
    )
    db.add(report)
    db.flush()
    for data, content_type in images:
        db.add(new_photo(data, content_type, current_user.id, report_id=report.id))
    log_action(db, "report_driver", "driver_report", str(report.id),
               f"{report.plate_number}: {report.violation_type} at {report.location} "
               f"({len(images)} photo(s){', video link' if report.evidence_url else ''})", current_user.id)
    broadcast(
        db, "driver_report_received",
        {"reporter": current_user.full_name, "plate": report.plate_number,
         "offence": _offence(report.violation_type), "place": report.location},
        roles=[UserRole.OFFICER.value, UserRole.ADMIN.value],
    )
    db.commit()
    db.refresh(report)
    return _out(db, report, for_officer=False)


@router.get("/mine", response_model=list[DriverReportOut])
def my_reports(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    reports = (
        db.query(DriverReport)
        .filter(DriverReport.reporter_id == current_user.id)
        .order_by(DriverReport.created_at.desc())
        .all()
    )
    return [_out(db, r, for_officer=False) for r in reports]


@router.get("/", response_model=list[DriverReportOut])
def list_reports(
    status_filter: DriverReportStatus | None = Query(DriverReportStatus.PENDING, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Reports for officers to review: waiting ones by default, oldest first so none sit forgotten."""
    query = db.query(DriverReport)
    if status_filter:
        query = query.filter(DriverReport.status == status_filter.value)
    order = DriverReport.created_at.asc() if status_filter == DriverReportStatus.PENDING else DriverReport.reviewed_at.desc()
    return [_out(db, r, for_officer=True) for r in query.order_by(order).limit(limit).all()]


@router.get("/{report_id}/photos/{photo_id}")
def report_photo(
    report_id: uuid.UUID,
    photo_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """A report's photo, for the citizen who sent it or an officer."""
    report = db.get(DriverReport, report_id)
    if report is None or (report.reporter_id != current_user.id and current_user.role not in OFFICERS):
        raise HTTPException(status_code=404, detail="Photo not found")
    photo = (
        db.query(ViolationPhoto)
        .filter(ViolationPhoto.id == photo_id, ViolationPhoto.report_id == report.id)
        .first()
    )
    if photo is None:
        raise HTTPException(status_code=404, detail="Photo not found")
    return photo_response(photo)


@router.post("/{report_id}/accept", response_model=DriverReportOut)
def accept_report(
    report_id: uuid.UUID,
    payload: DriverReportAccept,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """The evidence holds up: raise the violation and fine, with the citizen's photos on it."""
    report = _pending(db, report_id)
    vehicle = (
        find_vehicle_by_plate(db, payload.plate_number) if payload.plate_number
        else (db.get(Vehicle, report.vehicle_id) if report.vehicle_id else None)
    )
    if vehicle is None:
        raise HTTPException(
            status_code=422,
            detail="No registered vehicle has this plate. Correct the plate from the evidence, or reject the report.",
        )
    offence = payload.violation_type or ViolationType(report.violation_type)
    try:
        violation_in = ViolationCreate(
            vehicle_id=vehicle.id,
            violation_type=offence,
            location=report.location,
            latitude=report.latitude, longitude=report.longitude, gps_accuracy_m=report.gps_accuracy_m,
            evidence_source="citizen_report",
            evidence_url=report.evidence_url,
            timestamp=report.observed_at,
            description=f"From citizen report {report.reference}: {report.description}"[:1000],
        )
    except ValidationError as exc:
        # e.g. changing it to speeding, which needs a measured speed the report can't have.
        raise RequestValidationError(exc.errors())
    violation = record_violation_for(db, violation_in, current_user)

    # The evidence moves to the fine as it is: same rows, same fingerprints.
    db.query(ViolationPhoto).filter(ViolationPhoto.report_id == report.id).update(
        {ViolationPhoto.violation_id: violation.id}, synchronize_session=False
    )
    report.status = DriverReportStatus.ACCEPTED.value
    report.plate_number = vehicle.registration_number
    report.vehicle_id = vehicle.id
    report.violation_id = violation.id
    report.review_note = payload.note
    report.reviewed_by = current_user.id
    report.reviewed_at = utcnow()
    log_action(db, "accept_driver_report", "driver_report", str(report.id),
               f"Accepted {report.reference}: {offence.value} by {vehicle.registration_number}", current_user.id)
    notify(db, report.reporter_id, "driver_report_accepted",
           {"report": report.reference, "plate": vehicle.registration_number, "offence": _offence(offence.value)})
    db.commit()
    db.refresh(report)
    return _out(db, report, for_officer=True)


@router.post("/{report_id}/reject", response_model=DriverReportOut)
def reject_report(
    report_id: uuid.UUID,
    payload: DriverReportReject,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(*OFFICERS)),
):
    """Close a report without a fine. ``false_report`` fines the reporter instead."""
    report = _pending(db, report_id)
    report.status = (DriverReportStatus.FALSE if payload.false_report else DriverReportStatus.REJECTED).value
    report.review_note = payload.reason
    report.reviewed_by = current_user.id
    report.reviewed_at = utcnow()
    db.flush()
    log_action(db, "reject_driver_report", "driver_report", str(report.id),
               f"{'False report' if payload.false_report else 'Rejected'} {report.reference}: {payload.reason}",
               current_user.id)
    reporter = db.get(User, report.reporter_id)
    variables = {"report": report.reference, "plate": report.plate_number,
                 "offence": _offence(report.violation_type), "reason": payload.reason}
    if payload.false_report and reporter is not None:
        challan = fine_false_report(db, reporter, report.observed_at, report.location,
                                    f"False report of a driver ({report.reference}): {payload.reason}", current_user)
        notify(db, reporter.id, "driver_report_false", {**variables, **false_report_notice(db, reporter, challan)})
    elif reporter is not None:
        notify(db, reporter.id, "driver_report_rejected", variables)
    db.commit()
    db.refresh(report)
    return _out(db, report, for_officer=True)
