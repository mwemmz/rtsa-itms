"""Citizen self-service portal — section 11."""

import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import (
    Application,
    ApplicationDocument,
    ApplicationStatus,
    ApplicationStatusHistory,
    User,
    UserRole,
)
from app.schemas.citizen import (
    ApplicationCreate,
    ApplicationDetailOut,
    ApplicationFulfillmentPatch,
    ApplicationOut,
    ApplicationStatusOut,
    DocumentUploadResponse,
    NotificationPrefsUpdate,
    UserOut,
    UserProfileUpdate,
)
from app.schemas.common import MessageResponse, PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/citizens", tags=["Citizen Portal"])
app_router = APIRouter(prefix="/v1/applications", tags=["Applications"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _app_out(app: Application) -> ApplicationOut:
    try:
        details = json.loads(app.details) if isinstance(app.details, str) else app.details
    except Exception:
        details = {}
    return ApplicationOut(
        id=app.id,
        reference_number=app.reference_number,
        application_type=app.application_type,
        status=app.status,
        details=details,
        notes=app.notes,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def _app_detail_out(app: Application) -> ApplicationDetailOut:
    base = _app_out(app)
    history = [
        {
            "from_status": h.from_status,
            "to_status": h.to_status,
            "reason": h.reason,
            "occurred_at": h.occurred_at,
        }
        for h in sorted(app.status_history, key=lambda x: x.occurred_at)
    ]
    return ApplicationDetailOut(**base.model_dump(), status_history=history)


# ---------------------------------------------------------------------------
# Citizen profile
# ---------------------------------------------------------------------------

@router.get("/me", response_model=UserOut)
def get_my_profile(current_user: User = Depends(get_current_user)):
    return current_user


@router.put("/me", response_model=UserOut)
def update_my_profile(
    body: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    before = {"full_name": current_user.full_name, "phone": current_user.phone}
    if body.full_name is not None:
        current_user.full_name = body.full_name
    if body.phone is not None:
        current_user.phone = body.phone
    if body.notify_sms is not None:
        current_user.notify_sms = body.notify_sms
    if body.notify_email is not None:
        current_user.notify_email = body.notify_email
    if body.notify_in_app is not None:
        current_user.notify_in_app = body.notify_in_app
    log_event(db, action="CITIZEN.PROFILE.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="USER", resource_id=current_user.id,
              before=before)
    db.commit()
    db.refresh(current_user)
    return current_user


@router.put("/me/notification-preferences", response_model=UserOut)
def update_notification_preferences(
    body: NotificationPrefsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.notify_sms is not None:
        current_user.notify_sms = body.notify_sms
    if body.notify_email is not None:
        current_user.notify_email = body.notify_email
    if body.notify_in_app is not None:
        current_user.notify_in_app = body.notify_in_app
    db.commit()
    db.refresh(current_user)
    return current_user


# ---------------------------------------------------------------------------
# Proxy endpoints (vehicles, licences, fines, accidents pull from Dev 1's tables)
# These return lightweight read-model data joined with payment status we own.
# ---------------------------------------------------------------------------

@router.get("/me/vehicles")
def my_vehicles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Vehicles registered to this citizen by NRC number."""
    from app.models.vehicles import Vehicle
    if not current_user.nrc_number:
        return {"data": [], "note": "No NRC number on file — cannot look up vehicles."}
    vehicles = db.query(Vehicle).filter(Vehicle.owner_nrc == current_user.nrc_number).all()
    return {
        "data": [
            {
                "id": v.id,
                "plate_number": v.plate_number,
                "make": v.make,
                "model": v.model,
                "year": v.year,
                "color": v.color,
                "category": v.category.value,
                "status": v.status.value,
                "is_roadworthy": v.is_roadworthy,
                "registration_expiry": v.registration_expiry.isoformat() if v.registration_expiry else None,
            }
            for v in vehicles
        ]
    }


@router.get("/me/licences")
def my_licences(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Driving licence linked to this citizen's NRC."""
    from app.models.drivers import Driver
    if not current_user.nrc_number:
        return {"data": [], "note": "No NRC number on file."}
    driver = db.query(Driver).filter(Driver.nrc_number == current_user.nrc_number).first()
    if not driver:
        return {"data": []}
    return {
        "data": [{
            "id": driver.id,
            "licence_number": driver.licence_number,
            "licence_class": driver.licence_class.value,
            "licence_status": driver.licence_status.value,
            "expiry_date": driver.expiry_date.isoformat() if driver.expiry_date else None,
            "demerit_points": driver.demerit_points,
        }]
    }


@router.get("/me/fines")
def my_fines(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Outstanding and historic violations/fines for this citizen."""
    from app.models.violations import Violation, ViolationStatus
    from app.models.payments import PaymentTransaction, PaymentStatus
    if not current_user.nrc_number:
        return {"data": []}

    violations = (
        db.query(Violation)
        .filter(Violation.offender_nrc == current_user.nrc_number)
        .order_by(Violation.occurred_at.desc())
        .limit(100)
        .all()
    )

    # Cross-reference payment status from our payments table
    settled_refs = {
        t.reference_id
        for t in db.query(PaymentTransaction).filter(
            PaymentTransaction.payer_id == current_user.id,
            PaymentTransaction.status == PaymentStatus.SETTLED,
        ).all()
        if t.reference_id
    }

    return {
        "data": [
            {
                "id": v.id,
                "challan_number": v.challan_number,
                "violation_type": v.violation_type.value,
                "description": v.description,
                "location": v.location,
                "occurred_at": v.occurred_at.isoformat(),
                "fine_amount_ngwee": v.fine_amount_ngwee,
                "status": v.status.value,
                "paid_at": v.paid_at.isoformat() if v.paid_at else None,
                "payment_settled": v.id in settled_refs or v.challan_number in settled_refs,
            }
            for v in violations
        ]
    }


@router.get("/me/accidents")
def my_accidents(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Accident records linked to this citizen's NRC."""
    from app.models.accidents import Accident
    if not current_user.nrc_number:
        return {"data": []}
    accidents = (
        db.query(Accident)
        .filter(Accident.driver_nrc == current_user.nrc_number)
        .order_by(Accident.occurred_at.desc())
        .limit(50)
        .all()
    )
    return {
        "data": [
            {
                "id": a.id,
                "report_number": a.report_number,
                "occurred_at": a.occurred_at.isoformat(),
                "location": a.location,
                "severity": a.severity.value,
                "status": a.status.value,
                "plate_number": a.plate_number,
                "fatalities": a.fatalities,
                "injuries": a.injuries,
            }
            for a in accidents
        ]
    }


# ---------------------------------------------------------------------------
# Applications
# ---------------------------------------------------------------------------

@app_router.post("", response_model=ApplicationOut, status_code=201)
def submit_application(
    body: ApplicationCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = Application(
        citizen_id=current_user.id,
        application_type=body.application_type,
        status=ApplicationStatus.SUBMITTED,
        details=json.dumps(body.details),
    )
    db.add(app)
    db.flush()

    # Initial status history entry
    db.add(ApplicationStatusHistory(
        application_id=app.id,
        from_status=None,
        to_status=ApplicationStatus.SUBMITTED.value,
        changed_by=current_user.id,
    ))

    log_event(db, action="CITIZEN.APPLICATION.SUBMITTED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="APPLICATION", resource_id=app.id,
              after={"type": body.application_type.value, "ref": app.reference_number})
    db.commit()
    db.refresh(app)
    return _app_out(app)


@app_router.get("", response_model=PagedResponse[ApplicationOut])
def list_applications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = Query(default=None),
    status: ApplicationStatus | None = Query(default=None),
):
    q = db.query(Application).filter(Application.citizen_id == current_user.id)
    if status:
        q = q.filter(Application.status == status)
    if cursor:
        q = q.filter(Application.id > cursor)
    q = q.order_by(Application.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=[_app_out(a) for a in items],
        pagination=PaginationMeta(
            next_cursor=items[-1].id if has_more else None,
            has_more=has_more,
            limit=limit,
        ),
    )


@app_router.get("/{app_id}", response_model=ApplicationDetailOut)
def get_application(
    app_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = db.get(Application, app_id)
    if not app or app.citizen_id != current_user.id:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Application not found."})
    return _app_detail_out(app)


@app_router.get("/{app_id}/status", response_model=ApplicationStatusOut)
def get_application_status(
    app_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = db.get(Application, app_id)
    if not app or app.citizen_id != current_user.id:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Application not found."})
    return ApplicationStatusOut(
        id=app.id,
        reference_number=app.reference_number,
        status=app.status,
        updated_at=app.updated_at,
    )


@app_router.post("/{app_id}/documents", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    app_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = db.get(Application, app_id)
    if not app or app.citizen_id != current_user.id:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Application not found."})
    if app.status in (ApplicationStatus.COMPLETED, ApplicationStatus.CANCELLED):
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": "Cannot upload to a closed application."})

    content = await file.read()
    storage_path = f"uploads/applications/{app_id}/{uuid.uuid4().hex}_{file.filename}"
    # TODO: replace with real object storage (S3/Cloudflare R2) in production
    doc = ApplicationDocument(
        application_id=app_id,
        filename=file.filename or "upload",
        content_type=file.content_type or "application/octet-stream",
        storage_path=storage_path,
        file_size_bytes=len(content),
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return DocumentUploadResponse(
        id=doc.id,
        filename=doc.filename,
        content_type=doc.content_type,
        uploaded_at=doc.uploaded_at,
    )


@app_router.post("/{app_id}/cancel", response_model=ApplicationOut)
def cancel_application(
    app_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    app = db.get(Application, app_id)
    if not app or app.citizen_id != current_user.id:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Application not found."})
    if app.status in (ApplicationStatus.COMPLETED, ApplicationStatus.CANCELLED,
                      ApplicationStatus.APPROVED):
        raise HTTPException(
            422,
            detail={"code": "VALIDATION_ERROR", "message": f"Cannot cancel application in status {app.status.value}."},
        )
    old_status = app.status.value
    app.status = ApplicationStatus.CANCELLED
    db.add(ApplicationStatusHistory(
        application_id=app.id,
        from_status=old_status,
        to_status=ApplicationStatus.CANCELLED.value,
        changed_by=current_user.id,
        reason="Citizen-initiated cancellation",
    ))
    log_event(db, action="CITIZEN.APPLICATION.CANCELLED", actor_id=current_user.id,
              actor_type=ActorType.CITIZEN, resource_type="APPLICATION", resource_id=app.id)
    db.commit()
    db.refresh(app)
    return _app_out(app)


# ---------------------------------------------------------------------------
# Service-to-service fulfillment endpoint (Dev 1 calls this)
# ---------------------------------------------------------------------------

@app_router.patch("/{app_id}/fulfillment", response_model=ApplicationOut)
def fulfillment_update(
    app_id: str,
    body: ApplicationFulfillmentPatch,
    db: Session = Depends(get_db),
    # Service-to-service — validated via X-Service-Key header in production gateway
    # For now any authenticated staff can call this
    current_user: User = Depends(require_roles(UserRole.OFFICER, UserRole.ADMIN, UserRole.INSPECTOR)),
):
    app = db.get(Application, app_id)
    if not app:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Application not found."})
    old_status = app.status.value
    app.status = body.status
    db.add(ApplicationStatusHistory(
        application_id=app.id,
        from_status=old_status,
        to_status=body.status.value,
        changed_by=current_user.id,
        reason=body.reason,
    ))
    log_event(db, action="CITIZEN.APPLICATION.FULFILLMENT_UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="APPLICATION", resource_id=app.id,
              before={"status": old_status}, after={"status": body.status.value})
    db.commit()
    db.refresh(app)
    return _app_out(app)
