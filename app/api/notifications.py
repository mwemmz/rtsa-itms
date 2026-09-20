from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPreference,
)
from app.models.user import User
from app.schemas.notification import NotificationResponse

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/", response_model=list[NotificationResponse])
def my_notifications(
    unread_only: bool = False,
    channel: NotificationChannel | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        query = query.filter(Notification.read == False)  # noqa: E712
    if channel:
        query = query.filter(Notification.channel == channel)
    else:
        # The inbox is the in-app feed; SMS/email rows are delivery records.
        query = query.filter(Notification.channel == NotificationChannel.IN_APP)
    return query.order_by(Notification.created_at.desc()).offset(skip).limit(limit).all()


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.channel == NotificationChannel.IN_APP,
            Notification.read == False,  # noqa: E712
        )
        .count()
    )
    return {"unread": count}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id, Notification.read == False)  # noqa: E712
        .update({"read": True})
    )
    db.commit()
    return {"marked": n}


@router.get("/preferences")
def get_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Which channels the user receives notifications on (default: all)."""
    disabled = {
        p.channel.value
        for p in db.query(NotificationPreference).filter(
            NotificationPreference.user_id == current_user.id,
            NotificationPreference.enabled == False,  # noqa: E712
        )
    }
    return {c.value: c.value not in disabled for c in NotificationChannel}


@router.put("/preferences")
def set_preferences(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Body: {"sms": false, "email": true}. In-app notifications cannot be disabled."""
    for name, enabled in payload.items():
        if name not in NotificationChannel._value2member_map_:
            raise HTTPException(status_code=422, detail=f"Unknown channel '{name}'")
        if name == "in_app" and not enabled:
            raise HTTPException(status_code=422, detail="In-app notifications cannot be disabled")
        channel = NotificationChannel(name)
        pref = (
            db.query(NotificationPreference)
            .filter(NotificationPreference.user_id == current_user.id, NotificationPreference.channel == channel)
            .first()
        )
        if pref is None:
            db.add(NotificationPreference(user_id=current_user.id, channel=channel, enabled=bool(enabled)))
        else:
            pref.enabled = bool(enabled)
    db.commit()
    return get_preferences(db, current_user)


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_read(
    notification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == current_user.id)
        .first()
    )
    if not notification:
        raise HTTPException(status_code=404, detail="Notification not found")
    notification.read = True
    db.commit()
    db.refresh(notification)
    return notification
