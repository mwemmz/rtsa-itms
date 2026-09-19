"""Notification Management — section 13."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.notifications import Notification, NotificationTemplate
from app.schemas.common import MessageResponse, PagedResponse, PaginationMeta
from app.schemas.notifications import (
    NotificationOut,
    NotificationTemplateCreate,
    NotificationTemplateOut,
    SendNotificationRequest,
    SendNotificationResponse,
)
from app.services.notifications import send_notification

router = APIRouter(prefix="/v1/notifications", tags=["Notifications"])


# ---------------------------------------------------------------------------
# Send (service-to-service interface for Dev 1)
# ---------------------------------------------------------------------------

@router.post("/send", response_model=SendNotificationResponse, status_code=202)
def send(
    body: SendNotificationRequest,
    current_user: User = Depends(
        require_roles(UserRole.OFFICER, UserRole.ADMIN, UserRole.INSPECTOR)
    ),
    db: Session = Depends(get_db),
):
    """
    Shared notification event interface.
    Dev 1 calls this for violations, expiry reminders, accidents, etc.
    Returns 202 — delivery is async.
    """
    recipient = db.get(User, body.recipient_citizen_id)
    if not recipient:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Recipient citizen not found."})

    notif = send_notification(
        db,
        template_key=body.template_key,
        recipient=recipient,
        channel_preference=body.channel_preference,
        variables=body.variables,
        correlation_id=body.correlation_id,
    )
    db.commit()
    return SendNotificationResponse(notification_id=notif.id)


# ---------------------------------------------------------------------------
# Templates (admin-managed)
# ---------------------------------------------------------------------------

@router.post("/templates", response_model=NotificationTemplateOut, status_code=201)
def create_template(
    body: NotificationTemplateCreate,
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
    db: Session = Depends(get_db),
):
    if db.query(NotificationTemplate).filter(NotificationTemplate.template_key == body.template_key).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Template key already exists."})
    tmpl = NotificationTemplate(**body.model_dump())
    db.add(tmpl)
    log_event(db, action="NOTIFICATION.TEMPLATE.CREATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="NOTIFICATION_TEMPLATE",
              after={"key": body.template_key})
    db.commit()
    db.refresh(tmpl)
    return tmpl


@router.get("/templates", response_model=list[NotificationTemplateOut])
def list_templates(
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.OFFICER)),
    db: Session = Depends(get_db),
):
    return db.query(NotificationTemplate).filter(NotificationTemplate.is_active == True).all()  # noqa: E712


# ---------------------------------------------------------------------------
# Notification list / detail
# ---------------------------------------------------------------------------

@router.get("", response_model=PagedResponse[NotificationOut])
def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = Query(default=None),
    citizen_id: str | None = Query(default=None),
):
    # Citizens see only their own; staff can filter by citizen_id
    if current_user.role == UserRole.CITIZEN:
        target_id = current_user.id
    else:
        target_id = citizen_id or current_user.id

    q = db.query(Notification).filter(Notification.recipient_id == target_id)
    if cursor:
        q = q.filter(Notification.id > cursor)
    q = q.order_by(Notification.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=[NotificationOut.model_validate(n) for n in items],
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/{notification_id}", response_model=NotificationOut)
def get_notification(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notif = db.get(Notification, notification_id)
    if not notif:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Notification not found."})
    if current_user.role == UserRole.CITIZEN and notif.recipient_id != current_user.id:
        raise HTTPException(403, detail={"code": "FORBIDDEN", "message": "Access denied."})
    return NotificationOut.model_validate(notif)
