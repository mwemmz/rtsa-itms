"""Authentication service: lockout, MFA, sessions and device tracking."""

import json
import secrets
from datetime import timedelta

from fastapi import HTTPException, Request, status
from jose import JWTError
from sqlalchemy.orm import Session

from app.core import totp
from app.core.config import settings
from app.core.crypto import decrypt, encrypt, sha256
from app.core.ratelimit import check_rate_limit, record_failure
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.core.timeutil import aware, utcnow
from app.models.platform import Device, LoginAttempt, UserSession
from app.models.user import User, UserRole
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify

STAFF_ROLES = {UserRole.OFFICER, UserRole.TOLL_OPERATOR, UserRole.ADMIN}
MFA_CHALLENGE_MINUTES = 5
RECOVERY_CODE_COUNT = 8


def client_ip(request: Request) -> str:
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # The rightmost entry is the one our own proxy appended; anything to its
            # left was supplied by the client and cannot be trusted.
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _fingerprint(request: Request) -> str:
    ua = request.headers.get("user-agent", "")
    lang = request.headers.get("accept-language", "")
    hint = request.headers.get("x-device-id", "")
    return sha256(f"{ua}|{lang}|{hint}")


def _label(user_agent: str) -> str:
    ua = user_agent.lower()
    os_name = next((n for k, n in (("windows", "Windows"), ("android", "Android"), ("iphone", "iPhone"),
                                   ("ipad", "iPad"), ("mac os", "macOS"), ("linux", "Linux")) if k in ua), "Unknown OS")
    browser = next((n for k, n in (("edg", "Edge"), ("chrome", "Chrome"), ("firefox", "Firefox"),
                                   ("safari", "Safari"), ("python", "API client"), ("curl", "curl"))
                    if k in ua), "Browser")
    return f"{browser} on {os_name}"


def _record_attempt(db: Session, email: str, ip: str, success: bool, reason: str | None = None) -> None:
    db.add(LoginAttempt(email=email[:200], ip_address=ip, success=success, reason=reason))


def register_device(db: Session, user: User, request: Request) -> tuple[Device, bool]:
    fp = _fingerprint(request)
    ua = request.headers.get("user-agent", "")[:300]
    ip = client_ip(request)
    device = db.query(Device).filter(Device.user_id == user.id, Device.fingerprint == fp).first()
    is_new = device is None
    now = utcnow()
    if is_new:
        device = Device(user_id=user.id, fingerprint=fp, user_agent=ua, label=_label(ua),
                        last_ip=ip, login_count=0, first_seen_at=now, last_seen_at=now)
        db.add(device)
    device.last_ip = ip
    device.last_seen_at = now
    device.login_count = (device.login_count or 0) + 1
    db.flush()
    return device, is_new


def issue_session(db: Session, user: User, request: Request) -> tuple[str, UserSession, bool]:
    device, is_new_device = register_device(db, user, request)
    if device.is_blocked:
        _record_attempt(db, user.email, client_ip(request), False, "blocked_device")
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This device has been blocked")
    now = utcnow()
    session = UserSession(
        user_id=user.id,
        device_id=device.id,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:300],
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(session)
    db.flush()
    user.last_login_at = now
    user.failed_login_count = 0
    user.locked_until = None
    token = create_access_token({"sub": str(user.id), "role": user.role.value, "sid": str(session.id)})
    log_action(db, "login", "session", str(session.id),
               f"ip={session.ip_address} device={device.label}", user.id)
    if is_new_device and db.query(Device).filter(Device.user_id == user.id).count() > 1:
        notify(db, user.id, "new_device_login", {"device": device.label or "Unknown device", "ip": session.ip_address or "unknown"})
    _record_attempt(db, user.email, session.ip_address or "unknown", True)
    db.commit()
    return token, session, is_new_device


def _mfa_challenge_token(user: User) -> str:
    return create_access_token(
        {"sub": str(user.id), "typ": "mfa"}, expires_delta=timedelta(minutes=MFA_CHALLENGE_MINUTES)
    )


def authenticate(db: Session, request: Request, email: str, password: str) -> dict:
    """Validate credentials. Returns a token payload dict.

    Enforces: per-IP throttling, per-account lockout, MFA challenge, audit trail.
    """
    ip = client_ip(request)
    ip_key = f"login:{ip}"
    if not check_rate_limit(ip_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many login attempts. Try again later.")

    user = db.query(User).filter(User.email == email).first()
    now = utcnow()

    if user is not None and user.locked_until and aware(user.locked_until) > now:
        minutes = max(1, int((aware(user.locked_until) - now).total_seconds() // 60) + 1)
        _record_attempt(db, email, ip, False, "locked")
        db.commit()
        raise HTTPException(status.HTTP_423_LOCKED, f"Account temporarily locked. Try again in {minutes} minute(s).")

    if user is None or not verify_password(password, user.hashed_password):
        record_failure(ip_key)
        reason = "unknown_user" if user is None else "bad_password"
        if user is not None:
            user.failed_login_count = (user.failed_login_count or 0) + 1
            limit = runtime_settings.get(db, "security.max_login_attempts")
            if user.failed_login_count >= limit:
                user.locked_until = now + timedelta(minutes=runtime_settings.get(db, "security.lockout_minutes"))
                user.failed_login_count = 0
                reason = "locked_out"
                log_action(db, "account_locked", "user", str(user.id), f"Too many failed logins from {ip}", None)
        _record_attempt(db, email, ip, False, reason)
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if not user.is_active:
        _record_attempt(db, email, ip, False, "inactive")
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account is deactivated")

    # NB: no reset(ip_key) on success - otherwise an attacker holding one valid account could
    # log in between guesses to wipe the per-IP failure window. It simply expires.

    if user.mfa_enabled:
        db.commit()
        return {"mfa_required": True, "mfa_token": _mfa_challenge_token(user), "token_type": "bearer"}

    token, _, _ = issue_session(db, user, request)
    return {
        "access_token": token,
        "token_type": "bearer",
        "mfa_setup_required": _mfa_setup_needed(db, user),
    }


def _mfa_setup_needed(db: Session, user: User) -> bool:
    return bool(
        runtime_settings.get(db, "security.require_mfa_staff")
        and user.role in STAFF_ROLES
        and not user.mfa_enabled
    )


def verify_mfa_login(db: Session, request: Request, mfa_token: str, code: str) -> dict:
    ip = client_ip(request)
    key = f"mfa:{ip}"
    if not check_rate_limit(key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Try again later.")
    try:
        payload = decode_token(mfa_token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "MFA challenge expired. Sign in again.")
    if payload.get("typ") != "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA challenge")
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None or not user.is_active or not user.mfa_enabled:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid MFA challenge")
    if user.locked_until and aware(user.locked_until) > utcnow():
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked")

    if not _check_second_factor(db, user, code):
        record_failure(key)
        user.failed_login_count = (user.failed_login_count or 0) + 1
        limit = runtime_settings.get(db, "security.max_login_attempts")
        if user.failed_login_count >= limit:
            user.locked_until = utcnow() + timedelta(minutes=runtime_settings.get(db, "security.lockout_minutes"))
            user.failed_login_count = 0
            log_action(db, "account_locked", "user", str(user.id), "Too many failed MFA codes", None)
        _record_attempt(db, user.email, ip, False, "bad_mfa_code")
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid verification code")
    token, _, _ = issue_session(db, user, request)
    return {"access_token": token, "token_type": "bearer"}


def _check_second_factor(db: Session, user: User, code: str) -> bool:
    secret = decrypt(user.mfa_secret_enc) if user.mfa_secret_enc else None
    if secret and totp.verify(secret, code):
        return True
    return _consume_recovery_code(db, user, code)


def _consume_recovery_code(db: Session, user: User, code: str) -> bool:
    if not user.mfa_recovery_hashes or not code:
        return False
    hashes: list[str] = json.loads(user.mfa_recovery_hashes)
    digest = sha256(code.strip().lower())
    if digest in hashes:
        hashes.remove(digest)
        user.mfa_recovery_hashes = json.dumps(hashes)
        log_action(db, "mfa_recovery_code_used", "user", str(user.id), f"{len(hashes)} codes left", user.id)
        return True
    return False


# --- MFA enrolment ---------------------------------------------------------

def begin_mfa_enrolment(db: Session, user: User) -> dict:
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA is already enabled")
    secret = totp.generate_secret()
    user.mfa_secret_enc = encrypt(secret)  # stored but inactive until confirmed
    db.commit()
    return {"secret": secret, "otpauth_uri": totp.provisioning_uri(secret, user.email)}


def confirm_mfa_enrolment(db: Session, user: User, code: str) -> list[str]:
    secret = decrypt(user.mfa_secret_enc) if user.mfa_secret_enc else None
    if not secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Start MFA setup first")
    if user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA is already enabled")
    if not totp.verify(secret, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid verification code")
    codes = [secrets.token_hex(4) for _ in range(RECOVERY_CODE_COUNT)]
    user.mfa_enabled = True
    user.mfa_recovery_hashes = json.dumps([sha256(c) for c in codes])
    log_action(db, "mfa_enabled", "user", str(user.id), None, user.id)
    db.commit()
    return codes


def disable_mfa(db: Session, user: User, password: str, code: str, actor: User | None = None) -> None:
    if not user.mfa_enabled:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "MFA is not enabled")
    if actor is None:  # self-service needs both factors
        if not verify_password(password, user.hashed_password) or not _check_second_factor(db, user, code):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Password or code incorrect")
    user.mfa_enabled = False
    user.mfa_secret_enc = None
    user.mfa_recovery_hashes = None
    log_action(db, "mfa_disabled", "user", str(user.id),
               "by admin" if actor else None, (actor or user).id)
    db.commit()


# --- passwords & sessions ---------------------------------------------------

def change_password(db: Session, user: User, current: str, new: str, keep_session: str | None) -> None:
    from app.core.security import validate_password_strength

    if not verify_password(current, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    problem = validate_password_strength(new, runtime_settings.get(db, "security.password_min_length"))
    if problem:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, problem)
    user.hashed_password = hash_password(new)
    user.password_changed_at = utcnow()
    revoke_user_sessions(db, user.id, "password_changed", except_session=keep_session)
    log_action(db, "change_password", "user", str(user.id), None, user.id)
    db.commit()


def revoke_user_sessions(db: Session, user_id, reason: str, except_session=None) -> int:
    now = utcnow()
    count = 0
    for s in db.query(UserSession).filter(UserSession.user_id == user_id, UserSession.revoked_at.is_(None)).all():
        if except_session is not None and str(s.id) == str(except_session):
            continue
        s.revoked_at = now
        s.revoked_reason = reason
        count += 1
    return count
