"""Audit log sink — called by every mutating operation across both services."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models.admin import ActorType, AuditLog


def log_event(
    db: Session,
    *,
    action: str,
    actor_id: str | None = None,
    actor_type: ActorType = ActorType.SYSTEM,
    resource_type: str | None = None,
    resource_id: str | None = None,
    before: Any = None,
    after: Any = None,
    correlation_id: str | None = None,
    request_id: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Write an immutable audit event and flush (but do not commit — caller owns the transaction)."""
    import json

    def _serialize(obj: Any) -> str | None:
        if obj is None:
            return None
        if isinstance(obj, str):
            return obj
        try:
            return json.dumps(obj, default=str)
        except Exception:
            return str(obj)

    entry = AuditLog(
        id=str(uuid.uuid4()),
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before_state=_serialize(before),
        after_state=_serialize(after),
        correlation_id=correlation_id,
        request_id=request_id,
        ip_address=ip_address,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(entry)
    db.flush()
    return entry
