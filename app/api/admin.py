from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core import permissions as rbac
from app.core.database import get_db
from app.core.permissions import require_permission
from app.core.security import hash_password, require_role, validate_password_strength
from app.core.timeutil import utcnow
from app.models.audit_log import AuditLog
from app.models.driver import Driver
from app.models.enforcement import Challan, ChallanStatus
from app.models.notification import Notification, NotificationRule, NotificationStatus
from app.models.payment import Payment
from app.models.platform import Device, LoginAttempt, RolePermission, UserSession
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.schemas.notification import NotificationRuleCreate, NotificationRuleResponse
from app.schemas.user import (
    AdminUserCreate,
    AdminUserUpdate,
    DeviceResponse,
    SessionResponse,
    UserResponse,
)
from app.services import auth as auth_service
from app.services import settings as runtime_settings
from app.services.audit import log_action

router = APIRouter(prefix="/api/admin", tags=["Admin"])


# --- User management ---------------------------------------------------------

@router.get("/users", response_model=list[UserResponse])
def list_users(
    search: str | None = Query(None),
    role: UserRole | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users:manage")),
):
    query = db.query(User)
    if search:
        like = f"%{search}%"
        query = query.filter((User.email.ilike(like)) | (User.full_name.ilike(like)))
    if role:
        query = query.filter(User.role == role)
    return query.order_by(User.created_at.desc()).offset(skip).limit(limit).all()


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: AdminUserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users:manage")),
):
    """Create an account with any role (the only way to create staff accounts)."""
    if payload.role != UserRole.CITIZEN and not rbac.user_has_permission(db, current_user, "roles:manage"):
        raise HTTPException(status_code=403, detail="Missing permission: roles:manage")
    problem = validate_password_strength(payload.password, runtime_settings.get(db, "security.password_min_length"))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        role=payload.role,
        password_changed_at=utcnow(),
    )
    db.add(user)
    db.flush()
    log_action(db, "create_user", "user", str(user.id), f"Created {payload.role.value} account", current_user.id)
    db.commit()
    db.refresh(user)
    return user


def _get_user(db: Session, user_id: str) -> User:
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.patch("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users:manage")),
):
    user = _get_user(db, user_id)
    changes = payload.model_dump(exclude_unset=True)
    if "role" in changes and changes["role"] != user.role:
        if not rbac.user_has_permission(db, current_user, "roles:manage"):
            raise HTTPException(status_code=403, detail="Missing permission: roles:manage")
    if user.id == current_user.id and (
        changes.get("is_active") is False or ("role" in changes and changes["role"] != UserRole.ADMIN)
    ):
        raise HTTPException(status_code=400, detail="You cannot deactivate or demote your own account")
    for field, value in changes.items():
        setattr(user, field, value)
    if changes.get("is_active") is False:
        auth_service.revoke_user_sessions(db, user.id, "account_deactivated")
    log_action(db, "update_user", "user", str(user.id), ", ".join(sorted(changes)) or "no changes", current_user.id)
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}/role", response_model=UserResponse)
def update_user_role(
    user_id: str,
    new_role: UserRole,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles:manage")),
):
    user = _get_user(db, user_id)
    if user.id == current_user.id and new_role != UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="You cannot demote your own account")
    user.role = new_role
    db.flush()
    auth_service.revoke_user_sessions(db, user.id, "role_changed")
    log_action(db, "update_role", "user", str(user.id), f"Set role to {new_role.value}", current_user.id)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/unlock", response_model=UserResponse)
def unlock_user(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("security:manage")),
):
    user = _get_user(db, user_id)
    user.locked_until = None
    user.failed_login_count = 0
    log_action(db, "unlock_user", "user", str(user.id), None, current_user.id)
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_user_password(
    user_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("users:manage")),
):
    user = _get_user(db, user_id)
    new_password = payload.get("new_password")
    if not isinstance(new_password, str):
        raise HTTPException(status_code=422, detail='Body must be {"new_password": "..."}')
    problem = validate_password_strength(new_password, runtime_settings.get(db, "security.password_min_length"))
    if problem:
        raise HTTPException(status_code=400, detail=problem)
    user.hashed_password = hash_password(new_password)
    user.password_changed_at = utcnow()
    auth_service.revoke_user_sessions(db, user.id, "password_reset")
    log_action(db, "reset_password", "user", str(user.id), None, current_user.id)
    db.commit()


@router.delete("/users/{user_id}/mfa", status_code=status.HTTP_204_NO_CONTENT)
def admin_disable_mfa(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("security:manage")),
):
    """Recovery path for a user who has lost their authenticator device."""
    user = _get_user(db, user_id)
    auth_service.disable_mfa(db, user, "", "", actor=current_user)


@router.get("/users/{user_id}/sessions", response_model=list[SessionResponse])
def user_sessions(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("security:manage")),
):
    return (
        db.query(UserSession)
        .filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
        .limit(50)
        .all()
    )


@router.post("/users/{user_id}/sessions/revoke")
def revoke_user_sessions(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("security:manage")),
):
    user = _get_user(db, user_id)
    n = auth_service.revoke_user_sessions(db, user.id, "revoked_by_admin")
    log_action(db, "revoke_sessions", "user", str(user.id), f"{n} sessions", current_user.id)
    db.commit()
    return {"revoked": n}


@router.get("/users/{user_id}/devices", response_model=list[DeviceResponse])
def user_devices(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("security:manage")),
):
    return db.query(Device).filter(Device.user_id == user_id).order_by(Device.last_seen_at.desc()).all()


@router.get("/login-attempts")
def login_attempts(
    email: str | None = None,
    success: bool | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("audit:read")),
):
    query = db.query(LoginAttempt)
    if email:
        query = query.filter(LoginAttempt.email == email)
    if success is not None:
        query = query.filter(LoginAttempt.success == success)
    rows = query.order_by(LoginAttempt.created_at.desc()).offset(skip).limit(limit).all()
    return [
        {
            "email": r.email,
            "ip_address": r.ip_address,
            "success": r.success,
            "reason": r.reason,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# --- RBAC matrix -------------------------------------------------------------

@router.get("/permissions")
def permission_matrix(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles:manage")),
):
    """Every permission and which roles currently hold it."""
    matrix = {role.value: sorted(rbac.permissions_for_role(db, role.value)) for role in UserRole}
    return {"permissions": rbac.PERMISSIONS, "roles": matrix}


@router.put("/permissions/{role}/{permission}")
def set_role_permission(
    role: UserRole,
    permission: str,
    granted: bool,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("roles:manage")),
):
    if permission not in rbac.PERMISSIONS:
        raise HTTPException(status_code=404, detail="Unknown permission")
    if role == UserRole.ADMIN:
        raise HTTPException(status_code=400, detail="The admin role always holds every permission")
    row = (
        db.query(RolePermission)
        .filter(RolePermission.role == role.value, RolePermission.permission == permission)
        .first()
    )
    if row is None:
        db.add(RolePermission(role=role.value, permission=permission, granted=granted))
    else:
        row.granted = granted
    log_action(db, "set_permission", "role", role.value, f"{permission}={granted}", current_user.id)
    db.commit()
    rbac.invalidate_cache()
    return {"role": role.value, "permissions": sorted(rbac.permissions_for_role(db, role.value))}


# --- System settings, rules & thresholds -------------------------------------

@router.get("/settings")
def list_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("settings:manage")),
):
    return runtime_settings.describe_all(db)


@router.put("/settings/{key}")
def update_setting(
    key: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("settings:manage")),
):
    if "value" not in payload:
        raise HTTPException(status_code=422, detail='Body must be {"value": ...}')
    try:
        row = runtime_settings.set_value(db, key, payload["value"], current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    log_action(db, "update_setting", "setting", key, f"= {row.value}", current_user.id)
    db.commit()
    return {"key": key, "value": row.value}


@router.delete("/settings/{key}", status_code=status.HTTP_204_NO_CONTENT)
def reset_setting(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("settings:manage")),
):
    if key not in runtime_settings.BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown setting")
    runtime_settings.reset(db, key)
    log_action(db, "reset_setting", "setting", key, "Reverted to default", current_user.id)
    db.commit()


# --- Audit -------------------------------------------------------------------

@router.get("/audit-logs", response_model=list[dict])
def audit_logs(
    entity_type: str | None = Query(None),
    actor_id: str | None = Query(None),
    action: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("audit:read")),
):
    query = db.query(AuditLog)
    if entity_type:
        query = query.filter(AuditLog.entity_type == entity_type)
    if actor_id:
        query = query.filter(AuditLog.actor_id == actor_id)
    if action:
        query = query.filter(AuditLog.action == action)
    logs = query.order_by(AuditLog.timestamp.desc()).offset(skip).limit(limit).all()
    return [
        {
            "id": str(log.id),
            "actor_id": str(log.actor_id) if log.actor_id else None,
            "action": log.action,
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "details": log.details,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
        }
        for log in logs
    ]


# --- Legacy summary reports (the full engine lives at /api/reports) ----------

@router.get("/reports/summary", response_model=dict)
def report_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return {
        "vehicles": db.query(Vehicle).count(),
        "drivers": db.query(Driver).count(),
        "challans_total": db.query(Challan).count(),
        "challans_unpaid": db.query(Challan).filter(Challan.status == ChallanStatus.UNPAID).count(),
        "challans_paid": db.query(Challan).filter(Challan.status == ChallanStatus.PAID).count(),
        "revenue_collected": db.query(func.sum(Payment.amount)).filter(Payment.status == "completed").scalar() or 0,
    }


@router.get("/reports/vehicles")
def vehicles_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    rows = db.query(Vehicle.status, func.count()).group_by(Vehicle.status).all()
    return {(s.value if hasattr(s, "value") else s): n for s, n in rows}


@router.get("/reports/violations")
def violations_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    from app.models.enforcement import Violation

    rows = db.query(Violation.violation_type, func.count()).group_by(Violation.violation_type).all()
    return {(t.value if hasattr(t, "value") else t): n for t, n in rows}


@router.get("/reports/revenue")
def revenue_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    day = func.date(Payment.created_at)
    rows = (
        db.query(day, func.coalesce(func.sum(Payment.amount), 0))
        .filter(Payment.status == "completed")
        .group_by(day)
        .order_by(day.desc())
        .limit(30)
        .all()
    )
    return [{"date": str(d), "total": t} for d, t in rows]


# --- Notification rules & delivery -------------------------------------------

@router.get("/notification-rules", response_model=list[NotificationRuleResponse])
def list_notification_rules(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    return db.query(NotificationRule).all()


@router.post("/notification-rules", response_model=NotificationRuleResponse, status_code=status.HTTP_201_CREATED)
def create_notification_rule(
    payload: NotificationRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    existing = db.query(NotificationRule).filter(NotificationRule.trigger_event == payload.trigger_event).first()
    if existing:
        raise HTTPException(status_code=400, detail="Rule for this event already exists")
    rule = NotificationRule(**payload.model_dump())
    db.add(rule)
    db.flush()
    log_action(db, "create_rule", "notification_rule", str(rule.id), payload.trigger_event, current_user.id)
    db.commit()
    db.refresh(rule)
    return rule


@router.patch("/notification-rules/{rule_id}", response_model=NotificationRuleResponse)
def update_notification_rule(
    rule_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    rule = db.query(NotificationRule).filter(NotificationRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    for field in ("channels", "title_template", "body_template", "is_active"):
        if field in payload:
            setattr(rule, field, payload[field])
    log_action(db, "update_rule", "notification_rule", str(rule.id), rule.trigger_event, current_user.id)
    db.commit()
    db.refresh(rule)
    return rule


@router.get("/notifications/stats")
def notification_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    """Delivery health: counts by channel and status, plus recent failures."""
    rows = db.query(Notification.channel, Notification.status, func.count()).group_by(
        Notification.channel, Notification.status
    ).all()
    stats: dict = {}
    for channel, st, n in rows:
        stats.setdefault(channel.value, {})[st.value] = n
    failures = (
        db.query(Notification)
        .filter(Notification.status == NotificationStatus.FAILED)
        .order_by(Notification.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "by_channel": stats,
        "recent_failures": [
            {"id": str(f.id), "channel": f.channel.value, "event": f.trigger_event, "error": f.last_error,
             "attempts": f.attempts}
            for f in failures
        ],
    }


@router.post("/notifications/broadcast")
def broadcast_notification(
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    """Send an ad-hoc announcement (title + message) to a role or everyone."""
    from app.models.notification import Notification as N, NotificationChannel

    title = str(payload.get("title", "")).strip()
    message = str(payload.get("message", "")).strip()
    if not title or not message:
        raise HTTPException(status_code=422, detail="title and message are required")
    roles = payload.get("roles")
    query = db.query(User).filter(User.is_active == True)  # noqa: E712
    if roles:
        query = query.filter(User.role.in_([str(r).lower() for r in roles]))
    users = query.all()
    for u in users:
        db.add(N(user_id=u.id, channel=NotificationChannel.IN_APP, trigger_event="announcement",
                 title=title[:200], body=message, status=NotificationStatus.SENT, sent_at=utcnow()))
    log_action(db, "broadcast", "notification", None, f"'{title[:60]}' to {len(users)} users", current_user.id)
    db.commit()
    return {"recipients": len(users)}
