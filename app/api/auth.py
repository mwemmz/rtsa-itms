import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.crypto import sha256
from app.core.permissions import permissions_for_role
from app.core.ratelimit import check_rate_limit, record_failure
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
    EmailRequest,
    LoginRequest,
    MFACodeRequest,
    MFADisableRequest,
    MFAVerifyRequest,
    PasswordChange,
    PasswordResetConfirm,
    SessionResponse,
    Token,
    TokenRequest,
    UserCreate,
    UserResponse,
)
from app.services import account_links
from app.services import auth as auth_service
from app.services import captcha as captcha_service
from app.services import settings as runtime_settings
from app.services.audit import log_action

router = APIRouter(prefix="/api/auth", tags=["Auth"])

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PHONE_PATTERN = re.compile(r"^\+?[0-9][0-9 ]{6,19}$")
REGISTRATIONS_PER_IP = 10  # per ratelimit.WINDOW_SECONDS - generous enough for a shared campus/office NAT

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


async def _login_fields(request: Request) -> dict:
    """Pull email/password plus optional CAPTCHA fields from JSON or OAuth2 form data."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
    else:
        form = await request.form()
        body = {
            "email": form.get("email") or form.get("username"),
            "password": form.get("password"),
            "captcha_id": form.get("captcha_id"),
            "captcha_answer": form.get("captcha_answer"),
            "captcha_token": form.get("captcha_token"),
        }
    return {
        "email": body.get("email") if isinstance(body.get("email"), str) else None,
        "password": body.get("password") if isinstance(body.get("password"), str) else None,
        "captcha_id": body.get("captcha_id"),
        "captcha_answer": body.get("captcha_answer"),
        "captcha_token": body.get("captcha_token"),
    }


@router.get("/captcha")
def get_captcha():
    """Public: fetch a CAPTCHA challenge to solve before registering or logging in.

    Returns a sandbox math challenge, or (when CAPTCHA_PROVIDER/keys are configured)
    the site key of the real provider the frontend should render its widget with.
    """
    return captcha_service.new_challenge()


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, background: BackgroundTasks, db: Session = Depends(get_db)):
    """Public self-registration. Always creates a *citizen* account."""
    ip_key = f"register:{auth_service.client_ip(request)}"
    if not check_rate_limit(ip_key, REGISTRATIONS_PER_IP):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many sign-ups from this network. Try again later.")
    record_failure(ip_key)  # every attempt counts towards the per-IP sign-up budget
    if runtime_settings.get(db, "security.captcha_enabled") and not captcha_service.verify(payload.model_dump()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CAPTCHA verification failed")
    if payload.role != UserRole.CITIZEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Staff accounts can only be created by an administrator",
        )
    email = payload.email.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.match(email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter a valid email address")
    full_name = " ".join(payload.full_name.split())
    if not full_name or len(full_name) > 120:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Enter your full name (up to 120 characters)")
    phone = (payload.phone_number or "").strip() or None
    if phone and not PHONE_PATTERN.match(phone):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Enter a valid mobile number, e.g. +260971234567")
    problem = validate_password_strength(
        payload.password, runtime_settings.get(db, "security.password_min_length")
    )
    if problem:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=problem)
    existing = db.query(User).filter(func.lower(User.email) == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            # If someone else registered their address, the real owner can take it back:
            # a reset link goes to their inbox, and using it also confirms the address.
            detail="Email already registered. If it's yours, use \"Forgot password\" to get in.",
        )
    user = User(
        email=email,
        hashed_password=hash_password(payload.password),
        full_name=full_name,
        phone_number=phone,
        role=UserRole.CITIZEN,
        password_changed_at=utcnow(),
        email_verified_at=None,  # until they use the confirmation link
    )
    db.add(user)
    db.flush()
    log_action(db, "register", "user", str(user.id), "Registered as citizen", user.id)
    db.commit()
    db.refresh(user)
    background.add_task(account_links.send, account_links.confirmation_email(user, account_links.public_base_url(request)))
    return user


# --- forgotten passwords and email confirmation ---------------------------------------------

LINK_REQUESTS_PER_IP = 5      # per ratelimit.WINDOW_SECONDS
LINK_REQUESTS_PER_EMAIL = 3   # stops anyone flooding one inbox with reset mail


def _find_by_email(db: Session, email: str) -> User | None:
    email = email.strip()
    return (db.query(User).filter(User.email == email).first()
            or db.query(User).filter(func.lower(User.email) == email.lower()).first())


def _throttle_link_requests(request: Request, kind: str, email: str) -> None:
    ip_key = f"{kind}:{auth_service.client_ip(request)}"
    email_key = f"{kind}-email:{sha256(email.strip().lower())[:24]}"
    if not check_rate_limit(ip_key, LINK_REQUESTS_PER_IP) or not check_rate_limit(email_key, LINK_REQUESTS_PER_EMAIL):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Try again in a few minutes.")
    record_failure(ip_key)
    record_failure(email_key)


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def request_password_reset(payload: EmailRequest, request: Request, background: BackgroundTasks,
                           db: Session = Depends(get_db)):
    """Email a password-reset link.

    Always gives the same answer, and the email goes out after the response, so
    this can't be used to find out whether an address has an account.
    """
    _throttle_link_requests(request, "pwreset", payload.email)
    user = _find_by_email(db, payload.email)
    if user is not None and user.is_active:
        background.add_task(account_links.send,
                            account_links.password_reset_email(user, account_links.public_base_url(request)))
        log_action(db, "password_reset_requested", "user", str(user.id),
                   f"from {auth_service.client_ip(request)}", None)
        db.commit()
    return {"detail": "If an account uses that email, we've sent it a link to reset the password."}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def confirm_password_reset(payload: PasswordResetConfirm, background: BackgroundTasks,
                           db: Session = Depends(get_db)):
    """Set a new password with an emailed link. Signs the account out everywhere."""
    user = account_links.user_for_reset(db, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This reset link is invalid, has expired or was already used. Ask for a new one.")
    problem = validate_password_strength(payload.new_password,
                                         runtime_settings.get(db, "security.password_min_length"))
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user.hashed_password = hash_password(payload.new_password)  # this alone invalidates the link
    user.password_changed_at = utcnow()
    user.failed_login_count = 0
    user.locked_until = None
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()  # opening the emailed link proves they read this inbox
    auth_service.revoke_user_sessions(db, user.id, "password_reset")
    log_action(db, "reset_password", "user", str(user.id), "Self-service reset with an emailed link", user.id)
    notice = account_links.password_changed_email(user)
    db.commit()
    background.add_task(account_links.send, notice)


@router.post("/verify-email")
def verify_email(payload: TokenRequest, db: Session = Depends(get_db)):
    """Confirm the account's email address with the emailed link. Safe to repeat."""
    user = account_links.user_for_verification(db, payload.token)
    if user is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "This confirmation link is invalid or has expired. Ask for a new one.")
    if user.email_verified_at is None:
        user.email_verified_at = utcnow()
        log_action(db, "verify_email", "user", str(user.id), None, user.id)
        db.commit()
    return {"detail": "Email address confirmed.", "email": user.email}


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
def resend_email_confirmation(payload: EmailRequest, request: Request, background: BackgroundTasks,
                              db: Session = Depends(get_db)):
    """Send a fresh confirmation link. Same answer whether or not the account exists."""
    _throttle_link_requests(request, "verify", payload.email)
    user = _find_by_email(db, payload.email)
    if user is not None and user.is_active and user.email_verified_at is None:
        background.add_task(account_links.send,
                            account_links.confirmation_email(user, account_links.public_base_url(request)))
    return {"detail": "If that account is waiting for confirmation, we've sent a new link."}


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
    fields = await _login_fields(request)
    email, password = fields["email"], fields["password"]
    if not email or not password:
        raise HTTPException(
            status_code=422,
            detail="Provide email and password as JSON body or OAuth2 form data",
        )
    if runtime_settings.get(db, "security.captcha_enabled") and not captcha_service.verify(fields):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CAPTCHA verification failed")
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
    data["email_verified"] = current_user.email_verified_at is not None
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
