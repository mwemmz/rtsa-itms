"""Outbound delivery of SMS and email notifications.

Providers are chosen by configuration:
* Email -> SMTP when ``SMTP_HOST`` is set, otherwise logged (sandbox).
* SMS   -> HTTP gateway when ``SMS_WEBHOOK_URL`` is set, otherwise logged.
Message bodies are never written to logs; only the masked recipient.
"""

import smtplib
import ssl
from email.message import EmailMessage

import httpx

from app.core.config import settings
from app.core.logging import get_logger
from app.models.notification import Notification, NotificationChannel
from app.models.user import User

logger = get_logger("delivery")

MAX_ATTEMPTS = 3


def _mask(value: str) -> str:
    return value[:3] + "***" + value[-2:] if len(value) > 6 else "***"


def send_email(to: str, subject: str, body: str) -> None:
    if not settings.SMTP_HOST:
        logger.info("[sandbox email] to=%s subject=%r", _mask(to), subject)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.SMTP_FROM, to, subject
    msg.set_content(body)
    context = ssl.create_default_context()
    if settings.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context, timeout=15) as smtp:
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as smtp:
            smtp.starttls(context=context)
            if settings.SMTP_USER:
                smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            smtp.send_message(msg)


def send_sms(to: str, text: str) -> None:
    if not settings.SMS_WEBHOOK_URL:
        logger.info("[sandbox sms] to=%s", _mask(to))
        return
    headers = {"Authorization": f"Bearer {settings.SMS_WEBHOOK_TOKEN}"} if settings.SMS_WEBHOOK_TOKEN else {}
    resp = httpx.post(settings.SMS_WEBHOOK_URL, json={"to": to, "message": text}, headers=headers, timeout=10)
    resp.raise_for_status()


def deliver(notification: Notification, user: User) -> None:
    """Send one notification over its channel. Raises on failure."""
    if notification.channel == NotificationChannel.EMAIL:
        send_email(user.email, notification.title, notification.body)
    elif notification.channel == NotificationChannel.SMS:
        if not user.phone_number:
            raise ValueError("User has no phone number")
        send_sms(user.phone_number, f"{notification.title}: {notification.body}")
    # in_app rows need no delivery


def dispatch_pending(db, batch: int = 50) -> dict[str, int]:
    """Deliver queued SMS/email notifications with bounded retries."""
    from app.core.timeutil import utcnow
    from app.models.notification import NotificationStatus

    pending = (
        db.query(Notification)
        .filter(Notification.status == NotificationStatus.PENDING)
        .order_by(Notification.created_at)
        .limit(batch)
        .all()
    )
    result = {"sent": 0, "failed": 0, "retry": 0}
    for note in pending:
        user = db.get(User, note.user_id)
        note.attempts = (note.attempts or 0) + 1
        try:
            if user is None:
                raise ValueError("Recipient no longer exists")
            deliver(note, user)
            note.status = NotificationStatus.SENT
            note.sent_at = utcnow()
            note.last_error = None
            result["sent"] += 1
        except Exception as exc:  # provider errors must never kill the worker
            note.last_error = str(exc)[:500]
            if note.attempts >= MAX_ATTEMPTS:
                note.status = NotificationStatus.FAILED
                result["failed"] += 1
            else:
                result["retry"] += 1
    if pending:
        db.commit()
    return result
