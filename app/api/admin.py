"""Administration — section 14: users, settings, thresholds, audit logs."""

import json
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, hash_password, require_admin, require_roles
from app.models.admin import ActorType, AuditLog, SystemSetting, SystemThreshold
from app.models.citizen import User, UserRole
from app.schemas.admin import (
    AuditLogOut,
    SettingOut,
    SettingUpdate,
    StaffUserCreate,
    StaffUserUpdate,
    ThresholdOut,
    ThresholdUpdate,
)
from app.schemas.citizen import UserOut
from app.schemas.common import MessageResponse, PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/admin", tags=["Administration"])


def _setting_out(s: SystemSetting) -> SettingOut:
    try:
        value = json.loads(s.value)
    except Exception:
        value = s.value
    return SettingOut(
        key=s.key,
        value=value,
        description=s.description,
        requires_approval=s.requires_approval,
        etag=s.etag,
        updated_at=s.updated_at,
    )


def _threshold_out(t: SystemThreshold) -> ThresholdOut:
    try:
        value = json.loads(t.value)
    except Exception:
        value = t.value
    return ThresholdOut(
        key=t.key,
        value=value,
        description=t.description,
        unit=t.unit,
        etag=t.etag,
        updated_at=t.updated_at,
    )


# ---------------------------------------------------------------------------
# Staff user management
# ---------------------------------------------------------------------------

@router.get("/users", response_model=PagedResponse[UserOut])
def list_users(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = Query(default=None),
    role: UserRole | None = Query(default=None),
    is_active: bool | None = Query(default=None),
):
    q = db.query(User).filter(User.role != UserRole.CITIZEN)
    if role:
        q = q.filter(User.role == role)
    if is_active is not None:
        q = q.filter(User.is_active == is_active)
    if cursor:
        q = q.filter(User.id > cursor)
    q = q.order_by(User.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(
            next_cursor=items[-1].id if has_more else None,
            has_more=has_more,
            limit=limit,
        ),
    )


@router.post("/users", response_model=UserOut, status_code=201)
def provision_staff_user(
    body: StaffUserCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Email already registered."})
    try:
        role = UserRole(body.role.upper())
    except ValueError:
        raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": f"Invalid role: {body.role}"})

    user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        phone=body.phone,
        role=role,
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    db.flush()
    log_event(db, action="ADMIN.USER.PROVISIONED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="USER", resource_id=user.id,
              after={"email": user.email, "role": role.value})
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserOut)
def update_user(
    user_id: str,
    body: StaffUserUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "User not found."})
    before = {"full_name": user.full_name, "is_active": user.is_active, "role": user.role.value}
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.phone is not None:
        user.phone = body.phone
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.role is not None:
        try:
            user.role = UserRole(body.role.upper())
        except ValueError:
            raise HTTPException(422, detail={"code": "VALIDATION_ERROR", "message": f"Invalid role: {body.role}"})
    log_event(db, action="ADMIN.USER.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="USER", resource_id=user_id,
              before=before, after={"is_active": user.is_active, "role": user.role.value})
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# System settings
# ---------------------------------------------------------------------------

@router.get("/settings", response_model=list[SettingOut])
def list_settings(
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [_setting_out(s) for s in db.query(SystemSetting).order_by(SystemSetting.key).all()]


@router.get("/settings/{key}", response_model=SettingOut)
def get_setting(
    key: str,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not s:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Setting not found."})
    return _setting_out(s)


@router.put("/settings/{key}", response_model=SettingOut)
def update_setting(
    key: str,
    body: SettingUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if not s:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Setting not found."})
    if s.etag != body.if_match:
        raise HTTPException(
            412,
            detail={"code": "VALIDATION_ERROR", "message": "ETag mismatch — concurrent modification detected."},
        )
    before_val = s.value
    s.value = json.dumps(body.value)
    s.updated_by = current_user.id
    s.etag = str(uuid.uuid4())
    log_event(db, action="ADMIN.SETTING.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="SYSTEM_SETTING", resource_id=key,
              before={"value": before_val}, after={"value": s.value})
    db.commit()
    db.refresh(s)
    return _setting_out(s)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

@router.get("/thresholds", response_model=list[ThresholdOut])
def list_thresholds(
    db: Session = Depends(get_db),
    # Dev 1 reads thresholds at runtime — allow any authenticated staff
    current_user: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR, UserRole.AUDITOR)
    ),
):
    return [_threshold_out(t) for t in db.query(SystemThreshold).order_by(SystemThreshold.key).all()]


@router.get("/thresholds/{key}", response_model=ThresholdOut)
def get_threshold(
    key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR, UserRole.AUDITOR)
    ),
):
    t = db.query(SystemThreshold).filter(SystemThreshold.key == key).first()
    if not t:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Threshold not found."})
    return _threshold_out(t)


@router.put("/thresholds/{key}", response_model=ThresholdOut)
def update_threshold(
    key: str,
    body: ThresholdUpdate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    t = db.query(SystemThreshold).filter(SystemThreshold.key == key).first()
    if not t:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Threshold not found."})
    if t.etag != body.if_match:
        raise HTTPException(
            412,
            detail={"code": "VALIDATION_ERROR", "message": "ETag mismatch — concurrent modification detected."},
        )
    before_val = t.value
    t.value = json.dumps(body.value)
    t.updated_by = current_user.id
    t.etag = str(uuid.uuid4())
    log_event(db, action="ADMIN.THRESHOLD.UPDATED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="SYSTEM_THRESHOLD", resource_id=key,
              before={"value": before_val}, after={"value": t.value})
    db.commit()
    db.refresh(t)
    return _threshold_out(t)


# ---------------------------------------------------------------------------
# Audit logs
# ---------------------------------------------------------------------------

@router.get("/audit-logs", response_model=PagedResponse[AuditLogOut])
def query_audit_logs(
    current_user: User = Depends(
        require_roles(UserRole.ADMIN, UserRole.AUDITOR)
    ),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = Query(default=None),
    actor_id: str | None = Query(default=None),
    resource_type: str | None = Query(default=None),
    action: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
):
    q = db.query(AuditLog)
    if actor_id:
        q = q.filter(AuditLog.actor_id == actor_id)
    if resource_type:
        q = q.filter(AuditLog.resource_type == resource_type)
    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if date_from:
        q = q.filter(AuditLog.occurred_at >= date_from)
    if date_to:
        q = q.filter(AuditLog.occurred_at <= date_to)
    if cursor:
        q = q.filter(AuditLog.id > cursor)
    q = q.order_by(AuditLog.occurred_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=[AuditLogOut.model_validate(a) for a in items],
        pagination=PaginationMeta(
            next_cursor=items[-1].id if has_more else None,
            has_more=has_more,
            limit=limit,
        ),
    )
