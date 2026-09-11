import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def log_action(
    db: Session,
    action: str,
    entity_type: str,
    entity_id: str | None = None,
    details: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> AuditLog:
    entry = AuditLog(
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )
    db.add(entry)
    db.flush()
    return entry
