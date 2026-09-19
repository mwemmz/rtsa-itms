"""Shared notification event interface.

Any module (violations, tolls, accidents, expiry scans...) raises an event with
``notify(db, user_id, "event_name", {...})`` or ``broadcast(...)``. The event
name is looked up in ``notification_rules`` to find the channels and templates;
each channel a user has enabled produces one ``Notification`` row.

* ``in_app`` rows are delivered instantly (they are the row).
* ``sms`` / ``email`` rows are queued as ``pending`` and sent by the worker
  (see ``app/services/delivery.py``), with retries and failure tracking.
"""

import uuid

from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models.notification import (
    Notification,
    NotificationChannel,
    NotificationPreference,
    NotificationRule,
    NotificationStatus,
)
from app.models.user import User

CHANNEL_VALUES = NotificationChannel._value2member_map_


def _render(template: str, variables: dict) -> str:
    try:
        return template.format(**variables)
    except (KeyError, IndexError, ValueError):
        return template


# Kept for backwards compatibility with earlier callers.
session_safe_format = _render


def _disabled_channels(db: Session, user_ids: list[uuid.UUID]) -> dict[uuid.UUID, set[str]]:
    if not user_ids:
        return {}
    out: dict[uuid.UUID, set[str]] = {}
    rows = (
        db.query(NotificationPreference)
        .filter(
            NotificationPreference.user_id.in_(user_ids),
            NotificationPreference.enabled == False,  # noqa: E712
        )
        .all()
    )
    for row in rows:
        out.setdefault(row.user_id, set()).add(row.channel.value)
    return out


def _active_rule(db: Session, trigger_event: str) -> NotificationRule | None:
    return (
        db.query(NotificationRule)
        .filter(
            NotificationRule.trigger_event == trigger_event,
            NotificationRule.is_active == True,  # noqa: E712
        )
        .first()
    )


def _already_sent(db: Session, user_id, trigger_event: str, dedupe_key: str | None) -> bool:
    if not dedupe_key:
        return False
    return (
        db.query(Notification.id)
        .filter(
            Notification.user_id == user_id,
            Notification.trigger_event == trigger_event,
            Notification.dedupe_key == dedupe_key,
        )
        .first()
        is not None
    )


def _create_for_users(
    db: Session,
    users: list[User],
    rule: NotificationRule,
    trigger_event: str,
    variables: dict,
    dedupe_key: str | None,
) -> list[Notification]:
    created: list[Notification] = []
    disabled = _disabled_channels(db, [u.id for u in users])
    title = _render(rule.title_template, variables)
    body = _render(rule.body_template, variables)
    now = utcnow()
    for user in users:
        if _already_sent(db, user.id, trigger_event, dedupe_key):
            continue
        for channel_name in (c.strip() for c in rule.channels.split(",")):
            if channel_name not in CHANNEL_VALUES or channel_name in disabled.get(user.id, set()):
                continue
            if channel_name == "sms" and not user.phone_number:
                continue  # nowhere to send it
            is_in_app = channel_name == "in_app"
            note = Notification(
                user_id=user.id,
                channel=NotificationChannel(channel_name),
                trigger_event=trigger_event,
                title=title,
                body=body,
                status=NotificationStatus.SENT if is_in_app else NotificationStatus.PENDING,
                sent_at=now if is_in_app else None,
                dedupe_key=dedupe_key,
            )
            db.add(note)
            created.append(note)
    db.flush()
    return created


def notify(
    db: Session,
    user_id: uuid.UUID,
    trigger_event: str,
    variables: dict | None = None,
    dedupe_key: str | None = None,
) -> list[Notification]:
    """Raise a notification event for one user, applying the admin-managed rule."""
    rule = _active_rule(db, trigger_event)
    if not rule:
        return []
    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        return []
    return _create_for_users(db, [user], rule, trigger_event, variables or {}, dedupe_key)


def broadcast(
    db: Session,
    trigger_event: str,
    variables: dict | None = None,
    roles: list[str] | None = None,
) -> list[Notification]:
    """Raise an event for every active user (optionally filtered by role)."""
    rule = _active_rule(db, trigger_event)
    if not rule:
        return []
    query = db.query(User).filter(User.is_active == True)  # noqa: E712
    if roles:
        query = query.filter(User.role.in_([r.lower() for r in roles]))
    return _create_for_users(db, query.all(), rule, trigger_event, variables or {}, None)
