"""Who may see which live-update event.

Every audited change is published to the live stream (``app/services/audit.py``).
The stream used to send every event to every connected user, so a citizen could
watch staff activity and other people's sign-ins. This module decides, per
viewer, whether an event is delivered and what it contains:

* **Public** changes (road incidents, broadcast announcements) go to everyone.
* Anything that is **about the viewer** - their own account, sessions, devices,
  vehicles, fines, payments, licence applications - goes to them. That's the
  event's ``audience``, resolved once when the event is published.
* **Operational** records (vehicles, fines, toll, inspections, ...) go to staff.
* **Administrative** records (accounts, sessions, settings, roles, agencies, ...)
  go only to holders of the matching permission.
* Anything not listed here goes only to ``audit:read`` holders (admins): new
  entity types stay private until someone decides otherwise.

Who performed the change (``actor_id``) is only included for ``audit:read``
holders - nobody else's screen needs it.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.core.permissions import permissions_for_role
from app.models.user import STAFF_ROLES, User

PUBLIC_ENTITIES = {"road_incident"}
PUBLIC_ACTIONS = {("notification", "broadcast")}

# Day-to-day records every staff role works with. Payments are here because
# settling a fine or toll changes the challan/toll lists staff are looking at;
# the event itself only carries an id, and the ledger API stays permission-gated.
STAFF_ENTITIES = {
    "vehicle", "driver", "challan", "violation", "payment", "toll_transaction",
    "toll_offline_event", "anpr_event", "inspection", "insurance",
    "licence_application", "psv_operator", "psv_permit", "accident",
    "road", "road_segment", "intersection",
}

# Administrative records: any one of the listed permissions is enough.
PERMISSION_ENTITIES: dict[str, tuple[str, ...]] = {
    "user": ("users:manage", "security:manage", "audit:read"),
    "session": ("security:manage", "audit:read"),
    "device": ("security:manage", "audit:read"),
    "role": ("roles:manage",),
    "setting": ("settings:manage",),
    "notification_rule": ("notifications:manage",),
    "notification": ("notifications:manage",),
    "agency": ("integrations:manage",),
    "reconciliation_run": ("payments:reconcile", "payments:view_all"),
    "report": ("reports:view",),
    "system": ("system:monitor",),
}

AUDIT_PERMISSION = "audit:read"


@dataclass(frozen=True)
class Viewer:
    user_id: str
    is_staff: bool
    permissions: frozenset[str]


def viewer_for(db: Session, user: User) -> Viewer:
    return Viewer(
        user_id=str(user.id),
        is_staff=user.role in STAFF_ROLES,
        permissions=frozenset(permissions_for_role(db, user.role.value)),
    )


def _can_see(viewer: Viewer, event: dict[str, Any]) -> bool:
    entity, action = event.get("entity"), event.get("action")
    if entity in PUBLIC_ENTITIES or (entity, action) in PUBLIC_ACTIONS:
        return True
    if viewer.user_id in (event.get("audience") or ()):
        return True
    if entity in STAFF_ENTITIES:
        return viewer.is_staff
    if entity in PERMISSION_ENTITIES:
        return any(p in viewer.permissions for p in PERMISSION_ENTITIES[entity])
    return AUDIT_PERMISSION in viewer.permissions


def visible_event(viewer: Viewer, event: dict[str, Any]) -> dict[str, Any] | None:
    """The event as this viewer should receive it, or None if they shouldn't."""
    if not _can_see(viewer, event):
        return None
    out = {k: v for k, v in event.items() if k not in ("audience", "actor_id")}
    if AUDIT_PERMISSION in viewer.permissions:
        out["actor_id"] = event.get("actor_id")
    return out


# --- audience: which users a change is about ------------------------------------------------

def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _vehicle_owner(db: Session, vehicle_id: Any) -> Any:
    from app.models.vehicle import Vehicle
    from app.services.ownership import owner_user_id

    # Same rule as notifications and the citizen portal (direct link or the owner's
    # national ID): anyone who is notified about a record also gets its live updates.
    vid = _uuid(vehicle_id)
    return owner_user_id(db, db.get(Vehicle, vid)) if vid else None


def _driver_user(db: Session, driver_id: Any) -> Any:
    from app.models.driver import Driver

    did = _uuid(driver_id)
    driver = db.get(Driver, did) if did else None
    return driver.user_id if driver else None


def _owners(db: Session, entity_type: str, eid: uuid.UUID) -> list[Any]:
    # Imports are local so this module can be imported from audit.py without cycles.
    if entity_type == "user":
        return [eid]
    if entity_type == "vehicle":
        return [_vehicle_owner(db, eid)]
    if entity_type == "driver":
        return [_driver_user(db, eid)]
    if entity_type in ("challan", "violation"):
        from app.models.enforcement import Challan, Violation

        row = db.get(Challan if entity_type == "challan" else Violation, eid)
        if row is None:
            return []
        return [_vehicle_owner(db, row.vehicle_id), _driver_user(db, row.driver_id)]
    if entity_type in ("toll_transaction", "inspection", "insurance", "psv_permit"):
        from app.models.inspection import Inspection
        from app.models.insurance import Insurance
        from app.models.psv import PSVPermit
        from app.models.toll import TollTransaction

        model = {"toll_transaction": TollTransaction, "inspection": Inspection,
                 "insurance": Insurance, "psv_permit": PSVPermit}[entity_type]
        row = db.get(model, eid)
        return [_vehicle_owner(db, row.vehicle_id)] if row else []
    if entity_type == "payment":
        from app.models.payment import Payment

        row = db.get(Payment, eid)
        return [row.paid_by] if row else []
    if entity_type == "licence_application":
        from app.models.licence import LicenceApplication

        row = db.get(LicenceApplication, eid)
        return [row.applicant_id] if row else []
    if entity_type in ("session", "device"):
        from app.models.platform import Device, UserSession

        row = db.get(UserSession if entity_type == "session" else Device, eid)
        return [row.user_id] if row else []
    return []


def audience_for(db: Session, entity_type: str, entity_id: Any, actor_id: Any) -> list[str]:
    """User ids a change is about: whoever made it plus whoever owns the record.

    Never raises - a failed lookup just narrows the audience to the actor, and
    must not break the write that triggered it.
    """
    ids: list[Any] = [actor_id]
    eid = _uuid(entity_id)
    if eid is not None:
        try:
            ids.extend(_owners(db, entity_type, eid))
        except Exception:  # noqa: BLE001 - visibility must never break the audited write
            pass
    return sorted({str(i) for i in ids if i is not None})
