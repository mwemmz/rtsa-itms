"""Small, shared input checks so every form rejects the same kind of mistake the same way."""

import re
from datetime import datetime, timedelta

_NAME_RE = re.compile(r"^[^\W\d_][\w .'\-]{0,118}$", re.UNICODE)
_PHONE_RE = re.compile(r"^(?:\+?260|0)\d{9}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


def clean(v: str, what: str = "This field", required: bool = True) -> str:
    """Trim, collapse spaces, and refuse HTML-looking text."""
    v = " ".join((v or "").split())
    if required and not v:
        raise ValueError(f"{what} cannot be empty")
    if "<" in v or ">" in v:
        raise ValueError(f"{what} cannot contain < or >")
    return v


def person_name(v: str, what: str = "Name") -> str:
    v = clean(v, what)
    if not _NAME_RE.match(v):
        raise ValueError(f"{what} must start with a letter and use only letters, spaces, dots, hyphens or apostrophes")
    return v


def phone(v: str | None, what: str = "Phone number") -> str | None:
    if v is None or not str(v).strip():
        return None
    shown = " ".join(str(v).split())
    if not _PHONE_RE.match(re.sub(r"[\s\-()]", "", shown)):
        raise ValueError(f"{what} must be a Zambian number like 0971234567 or +260971234567")
    return shown


def email(v: str | None, required: bool = True) -> str | None:
    v = (v or "").strip().lower()
    if not v:
        if required:
            raise ValueError("Email is required")
        return None
    if len(v) > 254 or not _EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address like name@example.com")
    return v


def id_number(v: str, what: str = "ID number") -> str:
    v = clean(v, what)
    if not re.fullmatch(r"[A-Za-z0-9/\- ]{3,50}", v) or not any(c.isdigit() for c in v):
        raise ValueError(f"{what} must be an NRC like 123456/78/1 or a registration number with digits")
    return v


def not_far_future(v: datetime | None, what: str = "The time") -> datetime | None:
    if v is not None:
        naive = v.replace(tzinfo=None) if v.tzinfo is None else v.astimezone().replace(tzinfo=None)
        if naive > datetime.utcnow() + timedelta(days=1):
            raise ValueError(f"{what} cannot be in the future")
    return v
