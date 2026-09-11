import uuid

from sqlalchemy.orm import Session

from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationRule,
    NotificationStatus,
)
from app.models.user import User


def notify(
    db: Session,
    user_id: uuid.UUID,
    trigger_event: str,
    variables: dict | None = None,
) -> list[Notification]:
    """Send a notification for a trigger event, applying rules from config.

    In a real deployment this would dispatch via SMS/email providers; here we
    create in-app records and mark them as sent (the sandbox behaviour).
    """
    variables = variables or {}
    created: list[Notification] = []

    rule = (
        db.query(NotificationRule)
        .filter(NotificationRule.trigger_event == trigger_event, NotificationRule.is_active == True)
        .first()
    )

    if not rule:
        return created

    for channel_name in rule.channels.split(","):
        channel_name = channel_name.strip()
        if channel_name not in NotificationChannel._value2member_map_:
            continue
        title = session_safe_format(rule.title_template, variables)
        body = session_safe_format(rule.body_template, variables)
        notification = Notification(
            user_id=user_id,
            channel=NotificationChannel(channel_name),
            trigger_event=trigger_event,
            title=title,
            body=body,
            status=NotificationStatus.SENT,
        )
        db.add(notification)
        created.append(notification)

    db.flush()
    return created


def broadcast(
    db: Session,
    trigger_event: str,
    variables: dict | None = None,
    roles: list[str] | None = None,
) -> list[Notification]:
    """Send a notification to every active user (optionally filtered by role)."""
    variables = variables or {}
    created: list[Notification] = []
    rule = (
        db.query(NotificationRule)
        .filter(NotificationRule.trigger_event == trigger_event, NotificationRule.is_active == True)
        .first()
    )
    if not rule:
        return created

    query = db.query(User).filter(User.is_active == True)
    if roles:
        query = query.filter(User.role.in_([r.lower() for r in roles]))
    users = query.all()

    for channel_name in rule.channels.split(","):
        channel_name = channel_name.strip()
        if channel_name not in NotificationChannel._value2member_map_:
            continue
        title = session_safe_format(rule.title_template, variables)
        body = session_safe_format(rule.body_template, variables)
        for user in users:
            notification = Notification(
                user_id=user.id,
                channel=NotificationChannel(channel_name),
                trigger_event=trigger_event,
                title=title,
                body=body,
                status=NotificationStatus.SENT,
            )
            db.add(notification)
            created.append(notification)

    db.flush()
    return created


def session_safe_format(template: str, variables: dict) -> str:
    try:
        return template.format(**variables)
    except (KeyError, IndexError):
        return template