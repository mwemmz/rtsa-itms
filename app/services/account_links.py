"""Emailed account links: password reset and email confirmation.

Both are short-lived signed tokens (the app's JWT signing key), so no table is
needed:

* **Password reset** (``RESET_MINUTES``) is bound to the account's current
  password hash, so it stops working the moment the password changes - it can be
  used once, and an older link dies when a newer one is used.
* **Email confirmation** (``VERIFY_HOURS``) is bound to the address it was sent
  to, so changing the email invalidates it.

Links carry the token after ``#`` (``/#/reset?token=...``): browsers never send
the fragment to the server, so tokens don't land in access logs or proxies. The
page posts it to the API instead.
"""

import hashlib
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Request
from jose import JWTError

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import create_access_token, decode_token
from app.models.user import User
from app.services import delivery

logger = get_logger("account_links")

RESET_MINUTES = 30
VERIFY_HOURS = 48
_RESET = "password_reset"
_VERIFY = "verify_email"


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def reset_token(user: User) -> str:
    return create_access_token(
        {"sub": str(user.id), "typ": _RESET, "pwh": _fingerprint(user.hashed_password)},
        expires_delta=timedelta(minutes=RESET_MINUTES),
    )


def verify_token(user: User) -> str:
    return create_access_token(
        {"sub": str(user.id), "typ": _VERIFY, "em": _fingerprint(user.email.lower())},
        expires_delta=timedelta(hours=VERIFY_HOURS),
    )


def _claims(token: str, typ: str) -> dict | None:
    try:
        claims = decode_token(token)
    except JWTError:
        return None
    return claims if claims.get("typ") == typ else None


def user_for_reset(db, token: str) -> User | None:
    """The account a reset link is for, or None if it's invalid, expired or already used."""
    claims = _claims(token, _RESET)
    if claims is None:
        return None
    user = db.get(User, _uuid(claims.get("sub")))
    if user is None or not user.is_active or claims.get("pwh") != _fingerprint(user.hashed_password):
        return None
    return user


def user_for_verification(db, token: str) -> User | None:
    """The account a confirmation link is for, or None if invalid, expired or for an old address."""
    claims = _claims(token, _VERIFY)
    if claims is None:
        return None
    user = db.get(User, _uuid(claims.get("sub")))
    if user is None or claims.get("em") != _fingerprint(user.email.lower()):
        return None
    return user


def _uuid(value):
    import uuid

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def public_base_url(request: Request) -> str:
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL.rstrip("/")
    base = str(request.base_url).rstrip("/")
    # Behind Render's proxy the app itself sees http://; the proxy says what the browser used.
    if settings.TRUST_PROXY_HEADERS and request.headers.get("x-forwarded-proto") == "https":
        base = "https://" + base.split("://", 1)[-1]
    return base


@dataclass(frozen=True)
class LinkEmail:
    """A ready-to-send message: built inside the request, sent after it."""

    to: str
    subject: str
    body: str
    link: str = ""


def send(mail: LinkEmail) -> None:
    """Runs as a background task after the response, so its timing reveals nothing."""
    try:
        delivery.send_email(mail.to, mail.subject, mail.body)
    except Exception as exc:  # noqa: BLE001 - the user can ask again; don't crash the task
        logger.error("Could not send %r email: %s", mail.subject, exc)
        return
    if mail.link and not settings.SMTP_HOST and settings.ENVIRONMENT != "production":
        # Sandbox email only logs the recipient; without the link nobody could test this locally.
        logger.warning("[development only] link for %r: %s", mail.subject, mail.link)


def password_reset_email(user: User, base_url: str) -> LinkEmail:
    link = f"{base_url}/#/reset?token={reset_token(user)}"
    return LinkEmail(user.email, "Reset your RTSA ITMS password", (
        f"Hello {user.full_name},\n\n"
        "Someone asked to reset the password for your RTSA ITMS account. To choose a new one, open this link "
        f"within {RESET_MINUTES} minutes:\n\n{link}\n\n"
        "If that wasn't you, ignore this email - your password stays the same.\n\n"
        "Road Transport and Safety Agency"
    ), link)


def confirmation_email(user: User, base_url: str) -> LinkEmail:
    link = f"{base_url}/#/verify?token={verify_token(user)}"
    return LinkEmail(user.email, "Confirm your email for RTSA ITMS", (
        f"Hello {user.full_name},\n\n"
        "Please confirm this is your email address so we can send you fine, licence and road alerts here. "
        f"Open this link within {VERIFY_HOURS} hours:\n\n{link}\n\n"
        "If you didn't create an RTSA ITMS account, ignore this email.\n\n"
        "Road Transport and Safety Agency"
    ), link)


def password_changed_email(user: User) -> LinkEmail:
    return LinkEmail(user.email, "Your RTSA ITMS password was changed", (
        f"Hello {user.full_name},\n\n"
        "The password for your RTSA ITMS account was just changed and you've been signed out everywhere. "
        "If that wasn't you, contact RTSA immediately.\n\n"
        "Road Transport and Safety Agency"
    ))


