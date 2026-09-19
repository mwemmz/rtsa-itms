"""Housekeeping for tables that grow with traffic.

Business records (payments, ledger, audit log, challans...) are never pruned
here. Only operational telemetry is: login attempts, integration call logs and
dead sessions. Retention windows are deliberately conservative.
"""

from datetime import timedelta

from sqlalchemy.orm import Session

from app.core.timeutil import utcnow
from app.models.platform import IntegrationLog, LoginAttempt, UserSession

LOGIN_ATTEMPT_DAYS = 90
INTEGRATION_LOG_DAYS = 90
DEAD_SESSION_DAYS = 30


def prune_old_records(db: Session) -> dict[str, int]:
    now = utcnow()
    deleted = {
        "login_attempts": db.query(LoginAttempt)
        .filter(LoginAttempt.created_at < now - timedelta(days=LOGIN_ATTEMPT_DAYS))
        .delete(synchronize_session=False),
        "integration_logs": db.query(IntegrationLog)
        .filter(IntegrationLog.created_at < now - timedelta(days=INTEGRATION_LOG_DAYS))
        .delete(synchronize_session=False),
        # Sessions that were revoked, or that expired, long ago. Live sessions are never touched.
        "sessions": db.query(UserSession)
        .filter(
            (UserSession.revoked_at < now - timedelta(days=DEAD_SESSION_DAYS))
            | (UserSession.expires_at < now - timedelta(days=DEAD_SESSION_DAYS))
        )
        .delete(synchronize_session=False),
    }
    db.commit()
    return deleted
