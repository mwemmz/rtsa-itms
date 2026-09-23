from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.timeutil import aware, utcnow
from app.models.platform import Device, UserSession
from app.models.user import STAFF_ROLES, User
from app.services import settings as runtime_settings

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

ALGORITHM = "HS256"
SESSION_TOUCH_SECONDS = 30  # don't write last_seen on every single request

# Bcrypt only uses the first 72 bytes of a password. Truncate explicitly so
# newer bcrypt releases (which reject long inputs instead of truncating) behave
# identically to login checks on the same input.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    return bcrypt.hashpw(
        password.encode("utf-8")[:_BCRYPT_MAX_BYTES], bcrypt.gensalt()
    ).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(
            plain.encode("utf-8")[:_BCRYPT_MAX_BYTES], hashed.encode("utf-8")
        )
    except ValueError:
        return False


def validate_password_strength(password: str, min_length: int) -> str | None:
    """Return a human-readable problem, or None if the password is acceptable."""
    if len(password) < min_length:
        return f"Password must be at least {min_length} characters"
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        return "Password must contain both letters and numbers"
    return None


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    to_encode.setdefault("typ", "access")
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])


def _unauthorized(detail: str = "Could not validate credentials") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


# Endpoints a staff member who still owes a mandatory MFA enrolment may still reach -
# just enough to set MFA up, inspect their own account and get out. Everything else
# is blocked while `security.require_mfa_staff` is on and enrolment is outstanding.
MFA_SETUP_EXEMPT_PATHS = {
    "/api/auth/me",
    "/api/auth/mfa/setup",
    "/api/auth/mfa/enable",
    "/api/auth/logout",
    "/api/auth/change-password",
}


def enforce_staff_mfa(user: User, path: str, db: Session) -> None:
    """Block staff who still owe a mandatory MFA enrolment, except on MFA-setup paths."""
    if (
        user.role in STAFF_ROLES
        and not user.mfa_enabled
        and runtime_settings.get(db, "security.require_mfa_staff")
        and path not in MFA_SETUP_EXEMPT_PATHS
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA enrolment is required for your role. Set it up via POST /api/auth/mfa/setup.",
        )


def get_user_from_token(token: str, db: Session) -> User:
    """Resolve a (still-valid) access token to a live User, or raise 401."""
    try:
        payload = decode_token(token)
    except JWTError:
        raise _unauthorized()
    user_id = payload.get("sub")
    session_id = payload.get("sid")
    if user_id is None or session_id is None or payload.get("typ") != "access":
        raise _unauthorized()

    session = db.get(UserSession, session_id)
    now = utcnow()
    if session is None or str(session.user_id) != str(user_id):
        raise _unauthorized()
    if session.revoked_at is not None:
        raise _unauthorized("Session has been signed out")
    if aware(session.expires_at) <= now:
        raise _unauthorized("Session expired")
    idle_limit = timedelta(minutes=runtime_settings.get(db, "security.session_idle_minutes"))
    if now - aware(session.last_seen_at) > idle_limit:
        session.revoked_at = now
        session.revoked_reason = "idle_timeout"
        db.commit()
        raise _unauthorized("Session timed out due to inactivity")

    user = db.query(User).filter(User.id == user_id).first()
    if user is None or not user.is_active:
        raise _unauthorized()

    if session.device_id is not None:
        device = db.get(Device, session.device_id)
        if device is not None and device.is_blocked:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This device has been blocked")

    if (now - aware(session.last_seen_at)).total_seconds() > SESSION_TOUCH_SECONDS:
        session.last_seen_at = now
        db.commit()

    user._session_id = session.id  # type: ignore[attr-defined]
    return user


def get_current_user(
    request: Request,
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = get_user_from_token(token, db)
    enforce_staff_mfa(user, request.url.path, db)
    return user


def require_role(*allowed_roles: str):
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role.value not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user
    return role_checker
