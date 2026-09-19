"""Notification service — renders templates, dispatches via channel, persists records."""

import json
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.citizen import User
from app.models.notifications import (
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
)

logger = get_logger(__name__)

# Pre-agreed template keys (Dev 1 calls these for violations, expiries, accidents)
SYSTEM_TEMPLATES = [
    {
        "template_key": "LICENCE_EXPIRY_30D",
        "channel": "AUTO",
        "subject": "Your driving licence expires in 30 days",
        "body_template": "Dear {full_name}, your driving licence {licenceNumber} expires on {expiryDate}. Please renew it before the expiry date to avoid penalties.",
        "description": "30-day licence expiry reminder",
    },
    {
        "template_key": "LICENCE_EXPIRY_7D",
        "channel": "AUTO",
        "subject": "URGENT: Driving licence expires in 7 days",
        "body_template": "Dear {full_name}, your driving licence {licenceNumber} expires on {expiryDate}. Renew immediately to avoid penalties.",
        "description": "7-day licence expiry reminder",
    },
    {
        "template_key": "INSURANCE_EXPIRY_30D",
        "channel": "AUTO",
        "subject": "Vehicle insurance expires in 30 days",
        "body_template": "Dear {full_name}, the insurance for vehicle {plateNumber} expires on {expiryDate}. Renew to stay compliant.",
        "description": "30-day insurance expiry reminder",
    },
    {
        "template_key": "INSURANCE_EXPIRY_7D",
        "channel": "AUTO",
        "subject": "URGENT: Vehicle insurance expires in 7 days",
        "body_template": "Dear {full_name}, insurance for {plateNumber} expires on {expiryDate}. Renew immediately.",
        "description": "7-day insurance expiry reminder",
    },
    {
        "template_key": "INSPECTION_DUE",
        "channel": "AUTO",
        "subject": "Vehicle inspection due",
        "body_template": "Dear {full_name}, vehicle {plateNumber} is due for inspection on {dueDate}. Book your inspection to remain roadworthy.",
        "description": "Inspection due reminder",
    },
    {
        "template_key": "CITATION_ISSUED",
        "channel": "AUTO",
        "subject": "Traffic citation issued",
        "body_template": "Dear {full_name}, a traffic citation ({citationNumber}) has been issued against you for {violationType} on {issuedAt}. Fine amount: ZMW {amount}. Pay via the RTSA portal.",
        "description": "Citation/e-Challan issued notification",
    },
    {
        "template_key": "FINE_OVERDUE",
        "channel": "AUTO",
        "subject": "Overdue fine — immediate action required",
        "body_template": "Dear {full_name}, fine {fineReference} of ZMW {amount} is now overdue. Immediate payment is required to avoid further action.",
        "description": "Fine overdue escalation",
    },
    {
        "template_key": "ACCIDENT_LOGGED",
        "channel": "AUTO",
        "subject": "Accident report logged",
        "body_template": "Dear {full_name}, an accident report ({reportNumber}) involving vehicle {plateNumber} has been logged on {reportDate}.",
        "description": "Accident report confirmation",
    },
    {
        "template_key": "TOLL_BLACKLIST_WARNING",
        "channel": "AUTO",
        "subject": "Toll compliance warning",
        "body_template": "Dear {full_name}, vehicle {plateNumber} has been flagged for unpaid toll charges. Please settle outstanding toll fees to avoid enforcement action.",
        "description": "Toll blacklist warning",
    },
    {
        "template_key": "APPLICATION_STATUS",
        "channel": "AUTO",
        "subject": "Application status update",
        "body_template": "Dear {full_name}, your application {referenceNumber} status has changed to {status}. {notes}",
        "description": "Application status change notification",
    },
    {
        "template_key": "ACCOUNT_LOCKED",
        "channel": "EMAIL",
        "subject": "Account locked — suspicious activity",
        "body_template": "Dear {full_name}, your RTSA ITMS account has been temporarily locked due to {reason}. It will unlock at {unlockTime}. If this was not you, contact support immediately.",
        "description": "Account brute-force lockout alert",
    },
    {
        "template_key": "PASSWORD_RESET",
        "channel": "EMAIL",
        "subject": "Password reset request",
        "body_template": "Dear {full_name}, use OTP {otp} to reset your password. It expires in 15 minutes. If you did not request this, ignore this message.",
        "description": "Password reset OTP",
    },
]


def seed_system_templates(db: Session) -> None:
    """Idempotent — only inserts templates that don't already exist."""
    for tmpl in SYSTEM_TEMPLATES:
        exists = db.query(NotificationTemplate).filter(
            NotificationTemplate.template_key == tmpl["template_key"]
        ).first()
        if not exists:
            db.add(NotificationTemplate(**tmpl))
    db.commit()


def _render_body(template_body: str, variables: dict) -> str:
    """Simple {key} substitution — no external dependency needed."""
    try:
        return template_body.format(**variables)
    except KeyError:
        return template_body  # partial render is acceptable


def _resolve_channel(
    preference: NotificationChannel,
    user: User,
    template: NotificationTemplate,
) -> NotificationChannel:
    """Resolve AUTO to a concrete channel based on user preferences."""
    if preference != NotificationChannel.AUTO:
        return preference
    # Priority: SMS > EMAIL > IN_APP, gated by user prefs
    if user.notify_sms and template.channel in (NotificationChannel.SMS, NotificationChannel.AUTO):
        return NotificationChannel.SMS
    if user.notify_email:
        return NotificationChannel.EMAIL
    if user.notify_in_app:
        return NotificationChannel.IN_APP
    return NotificationChannel.IN_APP  # fallback


def _dispatch(channel: NotificationChannel, user: User, subject: str | None, body: str) -> bool:
    """
    Stub dispatcher — in production this integrates with SMS/email providers.
    Returns True on success.
    """
    logger.info(
        "DISPATCH channel=%s recipient=%s subject=%r body_preview=%r",
        channel.value,
        user.email,
        subject,
        body[:80],
    )
    # TODO: integrate real SMS gateway (e.g. Africa's Talking) and email provider (e.g. SendGrid)
    return True


def send_notification(
    db: Session,
    *,
    template_key: str,
    recipient: User,
    channel_preference: NotificationChannel = NotificationChannel.AUTO,
    variables: dict | None = None,
    correlation_id: str | None = None,
) -> Notification:
    """Create a Notification record, render it, attempt dispatch and persist result."""
    variables = variables or {}
    # Inject user's name so templates can always reference {full_name}
    variables.setdefault("full_name", recipient.full_name)

    template = db.query(NotificationTemplate).filter(
        NotificationTemplate.template_key == template_key,
        NotificationTemplate.is_active == True,  # noqa: E712
    ).first()

    if not template:
        logger.warning("Template %r not found — sending raw body", template_key)
        body = f"Notification: {template_key} — {variables}"
        subject = None
        template_id = None
        resolved_channel = (
            channel_preference
            if channel_preference != NotificationChannel.AUTO
            else NotificationChannel.IN_APP
        )
    else:
        body = _render_body(template.body_template, variables)
        subject = template.subject
        template_id = template.id
        resolved_channel = _resolve_channel(channel_preference, recipient, template)

    notif = Notification(
        id=str(uuid.uuid4()),
        template_id=template_id,
        recipient_id=recipient.id,
        channel=resolved_channel,
        status=NotificationStatus.QUEUED,
        subject=subject,
        body=body,
        variables=json.dumps(variables),
        correlation_id=correlation_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(notif)
    db.flush()

    try:
        success = _dispatch(resolved_channel, recipient, subject, body)
        if success:
            notif.status = NotificationStatus.SENT
            notif.sent_at = datetime.now(timezone.utc)
        else:
            notif.status = NotificationStatus.FAILED
            notif.failed_at = datetime.now(timezone.utc)
            notif.failure_reason = "Dispatch returned failure"
    except Exception as exc:
        logger.error("Notification dispatch error: %s", exc)
        notif.status = NotificationStatus.FAILED
        notif.failed_at = datetime.now(timezone.utc)
        notif.failure_reason = str(exc)

    db.flush()
    return notif
