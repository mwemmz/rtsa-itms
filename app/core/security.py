"""JWT creation/validation, password hashing, RBAC dependency helpers."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt as _bcrypt_lib
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.citizen import User, UserRole, UserSession

# ---------------------------------------------------------------------------
# Password hashing — use bcrypt directly (passlib 1.7.4 incompatible with bcrypt 4+)
# ---------------------------------------------------------------------------

_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    # bcrypt max input is 72 bytes; pre-hash with SHA-256 to support longer passwords
    digest = hashlib.sha256(plain.encode()).hexdigest().encode()
    return _bcrypt_lib.hashpw(digest, _bcrypt_lib.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    digest = hashlib.sha256(plain.encode()).hexdigest().encode()
    try:
        return _bcrypt_lib.checkpw(digest, hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(
    user_id: str,
    role: str,
    permissions: list[str],
    session_id: str,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    expire = _utcnow() + timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "roles": [role],
        "permissions": permissions,
        "sid": session_id,
        "exp": expire,
        "iat": _utcnow(),
        "jti": str(uuid.uuid4()),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token() -> tuple[str, str]:
    """Returns (raw_token, hashed_token)."""
    raw = secrets.token_urlsafe(48)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": str(exc)},
        )


# ---------------------------------------------------------------------------
# Request dependency — current authenticated user
# ---------------------------------------------------------------------------

def _extract_token(credentials: HTTPAuthorizationCredentials | None) -> str:
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "UNAUTHENTICATED", "message": "Missing Authorization header."},
        )
    return credentials.credentials


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    token = _extract_token(credentials)
    payload = decode_token(token)
    user_id: str | None = payload.get("sub")
    session_id: str | None = payload.get("sid")
    if not user_id:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Invalid token."})

    user = db.get(User, user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "User not found or inactive."})

    # Verify session is still alive
    if session_id:
        sess = db.query(UserSession).filter(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.is_active == True,  # noqa: E712
        ).first()
        if sess:
            # Normalize both sides to naive UTC for SQLite compatibility
            exp = sess.expires_at
            now = _utcnow()
            if exp.tzinfo is not None:
                exp_naive = exp.replace(tzinfo=None)
            else:
                exp_naive = exp
            if exp_naive < now.replace(tzinfo=None):
                raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Session expired."})
        else:
            raise HTTPException(status_code=401, detail={"code": "UNAUTHENTICATED", "message": "Session revoked."})

    return user


# ---------------------------------------------------------------------------
# RBAC helpers
# ---------------------------------------------------------------------------

def require_roles(*allowed: UserRole):
    """FastAPI dependency factory — raises 403 if user's role is not in allowed."""
    def _dep(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "FORBIDDEN", "message": "Insufficient permissions."},
            )
        return current_user
    return _dep


# Convenience shortcuts
require_admin = require_roles(UserRole.ADMIN)
require_officer_or_admin = require_roles(UserRole.OFFICER, UserRole.ADMIN)
require_any_staff = require_roles(UserRole.OFFICER, UserRole.ADMIN, UserRole.INSPECTOR, UserRole.AUDITOR)


# ---------------------------------------------------------------------------
# OTP / MFA helpers
# ---------------------------------------------------------------------------

def generate_otp(length: int = 6) -> tuple[str, str]:
    """Returns (plain_otp, hashed_otp)."""
    otp = "".join([str(secrets.randbelow(10)) for _ in range(length)])
    hashed = hashlib.sha256(otp.encode()).hexdigest()
    return otp, hashed


def verify_otp(plain: str, hashed: str) -> bool:
    return hashlib.sha256(plain.encode()).hexdigest() == hashed
