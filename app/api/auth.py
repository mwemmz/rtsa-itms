from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import permissions_for_role
from app.core.security import (
    get_current_user,
    hash_password,
    require_role,
    validate_password_strength,
)
from app.core.timeutil import utcnow
from app.models.platform import Device, UserSession
from app.models.user import User, UserRole
from app.schemas.user import (
    DeviceResponse,
    LoginRequest,
    MFACodeRequest,
    MFADisableRequest,
    MFAVerifyRequest,
    PasswordChange,
    SessionResponse,
    Token,
    UserCreate,
    UserResponse,
)
from app.services import auth as auth_service
from app.services import settings as runtime_settings
from app.services.audit import log_action

router = APIRouter(prefix="/api/auth", tags=["Auth"])

_FORM_SCHEMA = {
    "type": "object",
    "required": ["username", "password"],
    "properties": {
        "grant_type": {"type": "string", "enum": ["password"]},
        "username": {"type": "string", "description": "Account email address"},
        "password": {"type": "string", "format": "password"},
        "scope": {"type": "string", "default": ""},
        "client_id": {"type": "string"},
        "client_secret": {"type": "string"},
    },
}


async def _credentials(request: Request) -> tuple[str | None, str | None]:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            return None, None
        email = body.get("email")
        password = body.get("password")
    else:
        form = await request.form()
        email = form.get("email") or form.get("username")
        password = form.get("password")
    return (email if isinstance(email, str) else None), (
        password if isinstance(password, str) else None
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    """Public self-registration. Always creates a *citizen* account."""
    if payload.role != UserRole.CITIZEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff accounts can only be created by an administrator",
        )
    problem = validate_password_strength(
        payload.password, runtime_settings.get(db, "security.password_min_length")
    )
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered",
        )
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        phone_number=payload.phone_number,
        role=UserRole.CITIZEN,
        password_changed_at=utcnow(),
    )
    db.add(user)
    db.flush()
    log_action(db, "register", "user", str(user.id), "Registered as citizen", user.id)
    db.commit()
    db.refresh(user)
    return user


@router.post(
    "/login",
    response_model=Token,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": LoginRequest.model_json_schema()},
                "application/x-www-form-urlencoded": {"schema": _FORM_SCHEMA},
            },
        }
    },
)
async def login(request: Request, db: Session = Depends(get_db)):
    email, password = await _credentials(request)
    if not email or not password:
        raise HTTPException(
            status_code=422,
            detail="Provide email and password as JSON body or OAuth2 form data",
        )
    return auth_service.authenticate(db, request, email, password)


@router.post("/mfa/verify", response_model=Token)
def mfa_verify(payload: MFAVerifyRequest, request: Request, db: Session = Depends(get_db)):
    """Second step of login for accounts with MFA enabled."""
    return auth_service.verify_mfa_login(db, request, payload.mfa_token, payload.code)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = db.get(UserSession, current_user._session_id)
    if session and session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = "logout"
    log_action(db, "logout", "session", str(current_user._session_id), None, current_user.id)
    db.commit()


@router.get("/me")
def get_me(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    data = UserResponse.model_validate(current_user).model_dump(mode="json")
    data["permissions"] = sorted(permissions_for_role(db, current_user.role.value))
    data["mfa_setup_required"] = auth_service._mfa_setup_needed(db, current_user)
    return data


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: PasswordChange,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    auth_service.change_password(
        db, current_user, payload.current_password, payload.new_password, current_user._session_id
    )


# --- MFA enrolment -----------------------------------------------------------

@router.post("/mfa/setup")
def mfa_setup(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start MFA enrolment: returns the TOTP secret and otpauth:// URI."""
    return auth_service.begin_mfa_enrolment(db, current_user)


@router.post("/mfa/enable")
def mfa_enable(
    payload: MFACodeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Confirm enrolment with a code from the authenticator app. Returns one-time recovery codes."""
    return {"recovery_codes": auth_service.confirm_mfa_enrolment(db, current_user, payload.code)}


@router.post("/mfa/disable", status_code=status.HTTP_204_NO_CONTENT)
def mfa_disable(
    payload: MFADisableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    auth_service.disable_mfa(db, current_user, payload.password, payload.code)


# --- Sessions & devices ------------------------------------------------------

@router.get("/sessions", response_model=list[SessionResponse])
def my_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    rows = (
        db.query(UserSession)
        .filter(UserSession.user_id == current_user.id, UserSession.revoked_at.is_(None))
        .order_by(UserSession.last_seen_at.desc())
        .limit(50)
        .all()
    )
    out = []
    for s in rows:
        item = SessionResponse.model_validate(s)
        item.is_current = str(s.id) == str(current_user._session_id)
        out.append(item)
    return out


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    session_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.user_id == current_user.id)
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.revoked_at is None:
        session.revoked_at = utcnow()
        session.revoked_reason = "revoked_by_user"
        log_action(db, "revoke_session", "session", str(session.id), None, current_user.id)
        db.commit()


@router.post("/sessions/revoke-others")
def revoke_other_sessions(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    n = auth_service.revoke_user_sessions(
        db, current_user.id, "revoked_by_user", except_session=current_user._session_id
    )
    log_action(db, "revoke_other_sessions", "user", str(current_user.id), f"{n} sessions", current_user.id)
    db.commit()
    return {"revoked": n}


@router.get("/devices", response_model=list[DeviceResponse])
def my_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return (
        db.query(Device)
        .filter(Device.user_id == current_user.id)
        .order_by(Device.last_seen_at.desc())
        .all()
    )


@router.patch("/devices/{device_id}", response_model=DeviceResponse)
def update_device(
    device_id: str,
    trusted: bool | None = None,
    blocked: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    device = (
        db.query(Device)
        .filter(Device.id == device_id, Device.user_id == current_user.id)
        .first()
    )
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if trusted is not None:
        device.is_trusted = trusted
    if blocked is not None:
        device.is_blocked = blocked
        if blocked:
            for s in db.query(UserSession).filter(
                UserSession.device_id == device.id, UserSession.revoked_at.is_(None)
            ):
                s.revoked_at = utcnow()
                s.revoked_reason = "device_blocked"
    log_action(db, "update_device", "device", str(device.id), f"trusted={trusted} blocked={blocked}", current_user.id)
    db.commit()
    db.refresh(device)
    return device


@router.get("/users", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(UserRole.ADMIN)),
):
    return db.query(User).all()
