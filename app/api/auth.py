"""Auth endpoints — section 15: login, MFA, refresh, logout, sessions, RBAC."""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    create_access_token,
    create_refresh_token,
    generate_otp,
    get_current_user,
    hash_password,
    require_admin,
    require_roles,
    verify_otp,
    verify_password,
)
from app.models.admin import ActorType
from app.models.citizen import (
    MfaChallenge,
    Role,
    User,
    UserRole,
    UserRoleAssignment,
    UserSession,
)
from app.schemas.auth import (
    AssignRoleRequest,
    LoginRequest,
    LockoutReleaseResponse,
    MfaChallengeRequest,
    MfaChallengeResponse,
    MfaVerifyRequest,
    PasswordResetConfirm,
    PasswordResetRequest,
    RefreshRequest,
    RoleCreate,
    RoleOut,
    RoleUpdate,
    SessionOut,
    TokenResponse,
)
from app.schemas.citizen import UserOut, UserRegister
from app.schemas.common import MessageResponse

router = APIRouter(prefix="/v1/auth", tags=["Auth"])
rbac_router = APIRouter(prefix="/v1/rbac", tags=["RBAC"])
security_router = APIRouter(prefix="/v1/security", tags=["Security"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Registration (citizen self-sign-up)
# ---------------------------------------------------------------------------

@router.post("/register", response_model=UserOut, status_code=201)
def register(body: UserRegister, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Email already registered."})
    user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        nrc_number=body.nrc_number,
        phone=body.phone,
        role=UserRole.CITIZEN,
    )
    db.add(user)
    db.flush()
    log_event(db, action="AUTH.USER.REGISTERED", actor_id=user.id, actor_type=ActorType.CITIZEN,
              resource_type="USER", resource_id=user.id)
    db.commit()
    db.refresh(user)
    return user


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user: User | None = db.query(User).filter(User.email == body.email).first()

    # Brute-force check
    if user and user.locked_until:
        locked = user.locked_until
        now = _utcnow()
        locked_naive = locked.replace(tzinfo=None) if locked.tzinfo else locked
        if locked_naive > now.replace(tzinfo=None):
            raise HTTPException(
                status_code=401,
                detail={"code": "ACCOUNT_LOCKED", "message": f"Account locked until {user.locked_until.isoformat()}."},
            )

    if not user or not verify_password(body.password, user.hashed_password):
        if user:
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.max_login_attempts:
                user.locked_until = _utcnow() + timedelta(minutes=settings.lockout_minutes)
                log_event(db, action="SECURITY.ACCOUNT.LOCKED", actor_id=user.id, actor_type=ActorType.SYSTEM,
                          resource_type="USER", resource_id=user.id)
            db.commit()
        raise HTTPException(
            status_code=401,
            detail={"code": "UNAUTHENTICATED", "message": "Invalid credentials."},
        )

    if not user.is_active:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Account deactivated."})

    # Reset failed attempts
    user.failed_login_attempts = 0
    user.locked_until = None

    # If MFA enabled, return a partial response — client must complete MFA
    if user.mfa_enabled:
        db.commit()
        return {"mfa_required": True, "user_id": user.id}

    # Create session
    raw_refresh, hashed_refresh = create_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hashed_refresh,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        expires_at=_utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    db.flush()

    access_token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        permissions=[],
        session_id=session.id,
    )
    log_event(db, action="AUTH.SESSION.CREATED", actor_id=user.id, actor_type=ActorType.CITIZEN,
              resource_type="SESSION", resource_id=session.id,
              ip_address=request.client.host if request.client else None)
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# MFA
# ---------------------------------------------------------------------------

@router.post("/mfa/challenge", response_model=MfaChallengeResponse)
def mfa_challenge(body: MfaChallengeRequest, db: Session = Depends(get_db)):
    user = db.get(User, body.user_id)
    if not user:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "User not found."})

    plain_otp, hashed_otp = generate_otp()
    challenge = MfaChallenge(
        user_id=user.id,
        otp_hash=hashed_otp,
        channel=body.channel,
        expires_at=_utcnow() + timedelta(minutes=15),
    )
    db.add(challenge)
    db.commit()

    # TODO: dispatch OTP via notifications service
    # In dev/sandbox log it
    import logging
    logging.getLogger(__name__).info("MFA OTP for user %s: %s", user.id, plain_otp)

    return MfaChallengeResponse(
        challenge_id=challenge.id,
        channel=body.channel,
        message=f"OTP sent via {body.channel}.",
    )


@router.post("/mfa/verify", response_model=TokenResponse)
def mfa_verify(body: MfaVerifyRequest, request: Request, db: Session = Depends(get_db)):
    challenge = db.get(MfaChallenge, body.challenge_id)
    if not challenge or challenge.used:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Invalid or expired OTP."})
    exp = challenge.expires_at
    exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
    if exp_naive < _utcnow().replace(tzinfo=None):
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Invalid or expired OTP."})

    if not verify_otp(body.otp, challenge.otp_hash):
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Incorrect OTP."})

    challenge.used = True
    user = db.get(User, challenge.user_id)

    raw_refresh, hashed_refresh = create_refresh_token()
    session = UserSession(
        user_id=user.id,
        refresh_token_hash=hashed_refresh,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        expires_at=_utcnow() + timedelta(days=settings.refresh_token_expire_days),
    )
    db.add(session)
    db.flush()

    access_token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        permissions=[],
        session_id=session.id,
    )
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=raw_refresh,
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


# ---------------------------------------------------------------------------
# Refresh / Logout
# ---------------------------------------------------------------------------

@router.post("/refresh", response_model=TokenResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    hashed = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    session = db.query(UserSession).filter(
        UserSession.refresh_token_hash == hashed,
        UserSession.is_active == True,  # noqa: E712
    ).first()
    if not session:
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Refresh token invalid or expired."})
    exp = session.expires_at
    exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
    if exp_naive < _utcnow().replace(tzinfo=None):
        raise HTTPException(401, detail={"code": "UNAUTHENTICATED", "message": "Refresh token invalid or expired."})

    user = db.get(User, session.user_id)
    access_token = create_access_token(
        user_id=user.id,
        role=user.role.value,
        permissions=[],
        session_id=session.id,
    )
    session.last_used_at = _utcnow()
    db.commit()

    return TokenResponse(
        access_token=access_token,
        refresh_token=body.refresh_token,  # same refresh token
        token_type="bearer",
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post("/logout", response_model=MessageResponse)
def logout(body: RefreshRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    hashed = hashlib.sha256(body.refresh_token.encode()).hexdigest()
    session = db.query(UserSession).filter(
        UserSession.refresh_token_hash == hashed,
        UserSession.user_id == current_user.id,
    ).first()
    if session:
        session.is_active = False
        db.commit()
    return MessageResponse(message="Logged out successfully.")


# ---------------------------------------------------------------------------
# Sessions / Device tracking
# ---------------------------------------------------------------------------

@router.get("/sessions", response_model=list[SessionOut])
def list_sessions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = db.query(UserSession).filter(
        UserSession.user_id == current_user.id,
        UserSession.is_active == True,  # noqa: E712
    ).order_by(UserSession.last_used_at.desc()).all()
    return sessions


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
def revoke_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.query(UserSession).filter(
        UserSession.id == session_id,
        UserSession.user_id == current_user.id,
    ).first()
    if not session:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Session not found."})
    session.is_active = False
    db.commit()
    return MessageResponse(message="Session revoked.")


# ---------------------------------------------------------------------------
# Password reset
# ---------------------------------------------------------------------------

@router.post("/password/reset-request", response_model=MessageResponse)
def password_reset_request(body: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if user:
        plain_otp, hashed_otp = generate_otp(8)
        challenge = MfaChallenge(
            user_id=user.id,
            otp_hash=hashed_otp,
            channel="EMAIL",
            expires_at=_utcnow() + timedelta(minutes=15),
        )
        db.add(challenge)
        db.commit()
        # TODO: dispatch via notifications service
        import logging
        logging.getLogger(__name__).info("Password reset OTP for %s: %s", user.email, plain_otp)
    # Always return 200 to prevent email enumeration
    return MessageResponse(message="If the email exists, a reset OTP has been sent.")


@router.post("/password/reset-confirm", response_model=MessageResponse)
def password_reset_confirm(body: PasswordResetConfirm, db: Session = Depends(get_db)):
    hashed_otp = hashlib.sha256(body.token.encode()).hexdigest()
    challenge = db.query(MfaChallenge).filter(
        MfaChallenge.otp_hash == hashed_otp,
        MfaChallenge.used == False,  # noqa: E712
    ).first()
    if not challenge:
        raise HTTPException(400, detail={"code": "VALIDATION_ERROR", "message": "Invalid or expired reset token."})
    exp = challenge.expires_at
    exp_naive = exp.replace(tzinfo=None) if exp.tzinfo else exp
    if exp_naive < _utcnow().replace(tzinfo=None):
        raise HTTPException(400, detail={"code": "VALIDATION_ERROR", "message": "Invalid or expired reset token."})

    user = db.get(User, challenge.user_id)
    user.hashed_password = hash_password(body.new_password)
    challenge.used = True
    log_event(db, action="AUTH.PASSWORD.RESET", actor_id=user.id, actor_type=ActorType.CITIZEN,
              resource_type="USER", resource_id=user.id)
    db.commit()
    return MessageResponse(message="Password reset successfully.")


# ---------------------------------------------------------------------------
# RBAC routes
# ---------------------------------------------------------------------------

@rbac_router.get("/roles", response_model=list[RoleOut])
def list_roles(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return db.query(Role).all()


@rbac_router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(body: RoleCreate, db: Session = Depends(get_db), current_user: User = Depends(require_admin)):
    import json as _json
    if db.query(Role).filter(Role.name == body.name).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Role name already exists."})
    role = Role(name=body.name, description=body.description, permissions=_json.dumps(body.permissions))
    db.add(role)
    log_event(db, action="RBAC.ROLE.CREATED", actor_id=current_user.id, actor_type=ActorType.ADMIN,
              resource_type="ROLE", after={"name": body.name})
    db.commit()
    db.refresh(role)
    return _role_out(role)


@rbac_router.put("/roles/{role_id}", response_model=RoleOut)
def update_role(
    role_id: str,
    body: RoleUpdate,
    if_match: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    import json as _json
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Role not found."})
    if if_match and role.etag != if_match:
        raise HTTPException(412, detail={"code": "VALIDATION_ERROR", "message": "ETag mismatch — concurrent modification detected."})
    if body.description is not None:
        role.description = body.description
    if body.permissions is not None:
        role.permissions = _json.dumps(body.permissions)
    role.etag = str(uuid.uuid4())
    log_event(db, action="RBAC.ROLE.UPDATED", actor_id=current_user.id, actor_type=ActorType.ADMIN,
              resource_type="ROLE", resource_id=role_id)
    db.commit()
    db.refresh(role)
    return _role_out(role)


@rbac_router.post("/users/{user_id}/roles", response_model=MessageResponse, status_code=201)
def assign_roles(
    user_id: str,
    body: AssignRoleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "User not found."})
    for role_id in body.role_ids:
        existing = db.query(UserRoleAssignment).filter(
            UserRoleAssignment.user_id == user_id,
            UserRoleAssignment.role_id == role_id,
        ).first()
        if not existing:
            db.add(UserRoleAssignment(user_id=user_id, role_id=role_id, assigned_by=current_user.id))
    db.commit()
    return MessageResponse(message="Roles assigned.")


# ---------------------------------------------------------------------------
# Security — device/lockout
# ---------------------------------------------------------------------------

@security_router.get("/devices")
def list_devices(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sessions = db.query(UserSession).filter(UserSession.user_id == current_user.id).all()
    return [SessionOut.model_validate(s) for s in sessions]


@security_router.post("/lockouts/{user_id}/release", response_model=LockoutReleaseResponse)
def release_lockout(
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "User not found."})
    user.locked_until = None
    user.failed_login_attempts = 0
    log_event(db, action="SECURITY.LOCKOUT.RELEASED", actor_id=current_user.id, actor_type=ActorType.ADMIN,
              resource_type="USER", resource_id=user_id)
    db.commit()
    return LockoutReleaseResponse(user_id=user_id, message="Account unlocked.")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _role_out(role: Role) -> RoleOut:
    import json as _json
    try:
        perms = _json.loads(role.permissions)
    except Exception:
        perms = []
    return RoleOut(id=role.id, name=role.name, description=role.description, permissions=perms, etag=role.etag)
