"""Background worker for notification dispatch and expiry checks.

Runs as a separate loop on Render. In a real deployment this would be a
proper job queue (e.g. Redis + RQ or Celery); for the solo-project scope it
polls the DB every N seconds for pending notifications, dispatches them, and
warns drivers whose licences are expiring in the next 30 days.

Usage:
    python -m app.workers.notification_worker
"""

import time
from datetime import datetime, timedelta

from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.models.driver import Driver
from app.models.notification import Notification, NotificationStatus
from app.models.user import User
from app.services.notifications import notify

logger = get_logger("notification_worker")

POLL_INTERVAL_SECONDS = 30
LICENCE_WARNING_DAYS = 30


def dispatch_pending() -> int:
    db = SessionLocal()
    try:
        pending = (
            db.query(Notification)
            .filter(Notification.status == NotificationStatus.PENDING)
            .limit(50)
            .all()
        )
        for notification in pending:
            # In production, dispatch via SMS/email provider here using
            # notification.channel. Sandbox: mark as sent.
            notification.status = NotificationStatus.SENT
        db.commit()
        return len(pending)
    finally:
        db.close()


def scan_licence_expiries() -> int:
    """Warn drivers whose licences expire within 30 days (once per day)."""
    db = SessionLocal()
    try:
        today = datetime.utcnow()
        horizon = today + timedelta(days=LICENCE_WARNING_DAYS)
        drivers = (
            db.query(Driver)
            .filter(
                Driver.user_id.isnot(None),
                Driver.licence_expiry_date >= today,
                Driver.licence_expiry_date <= horizon,
            )
            .all()
        )
        sent = 0
        for driver in drivers:
            oldest = (
                db.query(Notification)
                .filter(
                    Notification.user_id == driver.user_id,
                    Notification.trigger_event == "licence_expiring",
                )
                .order_by(Notification.created_at.desc())
                .first()
            )
            if oldest is not None and oldest.created_at.date() == today.date():
                continue
            created = notify(
                db,
                driver.user_id,
                "licence_expiring",
                {
                    "licence_number": driver.licence_number,
                    "expiry_date": driver.licence_expiry_date.strftime("%Y-%m-%d"),
                },
            )
            sent += len(created)
        if sent:
            db.commit()
        return sent
    finally:
        db.close()


def run() -> None:
    logger.info("Notification worker started (poll %ss)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            dispatched = dispatch_pending()
            if dispatched:
                logger.info("Dispatched %s notification(s)", dispatched)
            expiring = scan_licence_expiries()
            if expiring:
                logger.info("Sent %s licence-expiry warning(s)", expiring)
        except Exception as e:
            logger.error("Worker error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()