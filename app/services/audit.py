import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog
from app.services.events import hub

# actions listed here represent user-visible data changes; other audit rows
# (logins, lookups, read-only exports) are deliberately not broadcast.
_MUTATING_ACTIONS = {
    "register", "create", "update", "deregister", "suspend", "revoke_session",
    "revoke_other_sessions", "update_device", "create_user", "update_user",
    "update_role", "unlock_user", "reset_password", "revoke_sessions",
    "set_permission", "update_setting", "reset_setting", "create_rule",
    "update_rule", "broadcast", "apply", "theory_test", "practical_test",
    "issue_licence", "renew_licence", "schedule", "update_result",
    "generate_challan", "report", "report_incident", "resolve_incident",
    "toll_event", "queue_toll_event", "sync_toll_event", "reject_toll_event",
    "pay", "refund", "reconcile", "register_agency", "update_agency",
    "rotate_agency_key", "agency_upsert_policy", "agency_report_accident",
    "issue_permit", "update_profile", "capture", "logout", "login",
    "account_locked", "mfa_enabled", "mfa_disabled", "change_password",
}


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
    if action in _MUTATING_ACTIONS:
        hub.publish(
            {
                "entity": entity_type,
                "action": action,
                "entity_id": str(entity_id) if entity_id is not None else None,
                "actor_id": str(actor_id) if actor_id is not None else None,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
    return entry