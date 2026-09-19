"""Role-Based Access Control: a permission catalogue and a role→permission matrix.

Built-in defaults live here; admins can grant or revoke individual permissions
per role at runtime (stored in ``role_permissions``) without a deploy. The
``admin`` role always keeps every permission so nobody can lock themselves out.
"""

import threading
import time

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.platform import RolePermission
from app.models.user import User, UserRole

PERMISSIONS: dict[str, str] = {
    "users:manage": "Create, edit, deactivate and unlock user accounts",
    "roles:manage": "Change user roles and the role permission matrix",
    "settings:manage": "Edit system settings, rules and thresholds",
    "audit:read": "Read the audit log and login history",
    "security:manage": "Manage sessions, devices and MFA of other users",
    "reports:view": "View reports and analytics",
    "reports:export": "Export reports as CSV, Excel and PDF",
    "payments:view_all": "View every payment and the revenue ledger",
    "payments:refund": "Refund payments",
    "payments:reconcile": "Run and view gateway reconciliation",
    "notifications:manage": "Manage notification rules and broadcast messages",
    "integrations:manage": "Manage agency API clients and view integration monitoring",
    "system:monitor": "View performance metrics, backups and system health",
}

DEFAULT_ROLE_PERMISSIONS: dict[str, set[str]] = {
    UserRole.ADMIN.value: set(PERMISSIONS),
    UserRole.OFFICER.value: {"reports:view", "reports:export"},
    UserRole.TOLL_OPERATOR.value: {"reports:view"},
    UserRole.CITIZEN.value: set(),
}

_CACHE_TTL = 15.0
_lock = threading.Lock()
_cache: dict[str, tuple[float, set[str]]] = {}


def invalidate_cache() -> None:
    with _lock:
        _cache.clear()


def permissions_for_role(db: Session, role: str) -> set[str]:
    if role == UserRole.ADMIN.value:
        return set(PERMISSIONS)
    now = time.monotonic()
    with _lock:
        hit = _cache.get(role)
        if hit and now - hit[0] < _CACHE_TTL:
            return set(hit[1])
    granted = set(DEFAULT_ROLE_PERMISSIONS.get(role, set()))
    for row in db.query(RolePermission).filter(RolePermission.role == role).all():
        if row.granted:
            granted.add(row.permission)
        else:
            granted.discard(row.permission)
    with _lock:
        _cache[role] = (now, set(granted))
    return granted


def user_has_permission(db: Session, user: User, permission: str) -> bool:
    return permission in permissions_for_role(db, user.role.value)


def require_permission(permission: str):
    """FastAPI dependency: the caller's role must hold ``permission``."""
    from app.core.security import get_current_user

    if permission not in PERMISSIONS:
        raise ValueError(f"Unknown permission {permission!r}")

    def checker(
        current_user: User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_has_permission(db, current_user, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing permission: {permission}",
            )
        return current_user

    return checker
