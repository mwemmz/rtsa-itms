"""
Notification dispatch worker.

In production this runs as a background task / Celery worker that polls the
`notifications` table for QUEUED entries and retries FAILED ones.
For the demo it is invoked directly from the API layer (synchronous dispatch).

Run manually:
    python -m app.workers.notification_dispatch
"""

import time
from datetime import datetime, timezone

from app.core.db import SessionLocal
from app.core.logging import get_logger
from app.models.notifications import Notification, NotificationStatus
from app.services.notifications import _dispatch, _resolve_channel

logger = get_logger(__name__)

MAX_RETRIES = 3
POLL_INTERVAL_SECONDS = 30


def process_pending_notifications() -> int:
    """Dispatch all QUEUED notifications and retry eligible FAILED ones. Returns count processed."""
    db = SessionLocal()
    processed = 0
    try:
        pending = (
            db.query(Notification)
            .filter(
                Notification.status.in_([NotificationStatus.QUEUED]),
            )
            .limit(100)
            .all()
        )

        for notif in pending:
            recipient = notif.recipient
            if not recipient:
                notif.status = NotificationStatus.FAILED
                notif.failure_reason = "Recipient not found"
                db.flush()
                continue

            try:
                success = _dispatch(notif.channel, recipient, notif.subject, notif.body)
                if success:
                    notif.status = NotificationStatus.SENT
                    notif.sent_at = datetime.now(timezone.utc)
                else:
                    notif.retry_count += 1
                    if notif.retry_count >= MAX_RETRIES:
                        notif.status = NotificationStatus.FAILED
                        notif.failed_at = datetime.now(timezone.utc)
                        notif.failure_reason = "Max retries exceeded"
            except Exception as exc:
                logger.error("Error dispatching notification %s: %s", notif.id, exc)
                notif.retry_count += 1
                if notif.retry_count >= MAX_RETRIES:
                    notif.status = NotificationStatus.FAILED
                    notif.failed_at = datetime.now(timezone.utc)
                    notif.failure_reason = str(exc)

            processed += 1

        db.commit()
        logger.info("Processed %d notifications", processed)
        return processed
    except Exception as exc:
        db.rollback()
        logger.error("Notification worker error: %s", exc)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    logger.info("Notification dispatch worker starting (poll every %ds)", POLL_INTERVAL_SECONDS)
    while True:
        count = process_pending_notifications()
        if count:
            logger.info("Dispatched %d notifications", count)
        time.sleep(POLL_INTERVAL_SECONDS)
