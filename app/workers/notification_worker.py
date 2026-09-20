"""Background worker: notification delivery, expiry reminders, scheduled backups.

Runs as a separate service on Render. For a larger deployment swap the polling
loop for a real queue (Redis + RQ/Celery); the service functions it calls
(``dispatch_pending``, ``scan_expiries``) are already queue-friendly.

Usage:
    python -m app.workers.notification_worker
"""

import time
from datetime import datetime

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import get_logger
from app.services.delivery import dispatch_pending as _dispatch
from app.services.expiry import scan_expiries
from app.services.maintenance import prune_old_records

logger = get_logger("notification_worker")

POLL_INTERVAL_SECONDS = 30
EXPIRY_SCAN_EVERY_SECONDS = 3600
BACKUP_EVERY_SECONDS = 24 * 3600
MAINTENANCE_EVERY_SECONDS = 24 * 3600


def dispatch_pending() -> int:
    db = SessionLocal()
    try:
        result = _dispatch(db)
        return result["sent"] + result["failed"]
    finally:
        db.close()


def scan_licence_expiries() -> int:
    """Kept for compatibility: runs the full expiry scan, returns total sent."""
    db = SessionLocal()
    try:
        return sum(scan_expiries(db).values())
    finally:
        db.close()


def _run_backup() -> None:
    try:
        from scripts.backup import run_backup

        path = run_backup()
        logger.info("Scheduled backup written: %s", path)
    except Exception as e:  # never let a backup failure stop notifications
        logger.error("Scheduled backup failed: %s", e)


def run() -> None:
    logger.info("Worker started (poll %ss)", POLL_INTERVAL_SECONDS)
    last_scan = last_backup = last_maint = 0.0
    while True:
        try:
            dispatched = dispatch_pending()
            if dispatched:
                logger.info("Processed %s notification(s)", dispatched)
            now = time.monotonic()
            if now - last_scan >= EXPIRY_SCAN_EVERY_SECONDS:
                last_scan = now
                sent = scan_licence_expiries()
                if sent:
                    logger.info("Sent %s expiry reminder(s)", sent)
            if now - last_maint >= MAINTENANCE_EVERY_SECONDS:
                last_maint = now
                db = SessionLocal()
                try:
                    logger.info("Pruned old telemetry: %s", prune_old_records(db))
                finally:
                    db.close()
            if not settings.DATABASE_URL.startswith("sqlite") and now - last_backup >= BACKUP_EVERY_SECONDS:
                last_backup = now
                _run_backup()
        except Exception as e:
            logger.error("Worker error: %s", e)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
