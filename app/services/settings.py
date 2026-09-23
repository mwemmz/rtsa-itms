"""Runtime system settings: business rules and thresholds editable by admins.

Defaults are declared here (type, bounds, description). Values saved in
``system_settings`` override them. Reads are cached for a few seconds so hot
paths (login, notification scans) cost nothing extra.
"""

import threading
import time
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings as env
from app.models.platform import SystemSetting


@dataclass(frozen=True)
class SettingDef:
    key: str
    default: str
    type: str  # int | bool | str | float | intlist
    category: str
    description: str
    min: float | None = None
    max: float | None = None


DEFINITIONS: list[SettingDef] = [
    SettingDef("security.max_login_attempts", str(env.MAX_LOGIN_ATTEMPTS), "int", "security",
               "Failed logins before an account is temporarily locked", 3, 20),
    SettingDef("security.lockout_minutes", str(env.LOCKOUT_MINUTES), "int", "security",
               "How long an account stays locked after too many failures", 1, 1440),
    SettingDef("security.session_idle_minutes", str(env.SESSION_IDLE_MINUTES), "int", "security",
               "Sign users out after this many idle minutes", 5, 480),
    SettingDef("security.password_min_length", str(env.PASSWORD_MIN_LENGTH), "int", "security",
               "Minimum password length", 8, 64),
    SettingDef("security.require_mfa_staff", str(env.REQUIRE_MFA_FOR_STAFF).lower(), "bool", "security",
               "Require staff accounts (officer, toll operator, admin) to enrol in MFA before using the app"),
    SettingDef("security.captcha_enabled", "false", "bool", "security",
               "Require a solved CAPTCHA on login and registration"),
    SettingDef("notifications.expiry_reminder_days", "30,14,7,1", "intlist", "notifications",
               "Days before expiry at which reminders are sent (licence, insurance, fitness, permits)"),
    SettingDef("payments.max_amount", "100000000", "int", "payments",
               "Largest single payment accepted (ngwee)", 1, None),
    SettingDef("payments.currency", "ZMW", "str", "payments", "Currency code shown on receipts"),
    SettingDef("enforcement.challan_due_days", "30", "int", "enforcement",
               "Default number of days before an e-Challan becomes overdue", 1, 365),
    SettingDef("enforcement.late_penalty_percent", "10", "int", "enforcement",
               "Surcharge applied to overdue e-Challans (percent)", 0, 100),
    SettingDef("toll.compliance_target_ms", "500", "int", "toll",
               "Target latency for a toll-gate compliance decision", 50, 5000),
    SettingDef("reports.default_range_days", "90", "int", "reports",
               "Default look-back window for reports", 1, 3650),
]

BY_KEY = {d.key: d for d in DEFINITIONS}

_CACHE_TTL = 5.0
_lock = threading.Lock()
_cache: tuple[float, dict[str, str]] | None = None


def invalidate_cache() -> None:
    global _cache
    with _lock:
        _cache = None


def _stored(db: Session) -> dict[str, str]:
    global _cache
    now = time.monotonic()
    with _lock:
        if _cache and now - _cache[0] < _CACHE_TTL:
            return _cache[1]
    values = {row.key: row.value for row in db.query(SystemSetting).all()}
    with _lock:
        _cache = (now, values)
    return values


def _coerce(defn: SettingDef, raw: str):
    if defn.type == "int":
        return int(raw)
    if defn.type == "float":
        return float(raw)
    if defn.type == "bool":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if defn.type == "intlist":
        return [int(x) for x in str(raw).split(",") if x.strip()]
    return str(raw)


def get(db: Session, key: str):
    defn = BY_KEY[key]
    raw = _stored(db).get(key, defn.default)
    try:
        return _coerce(defn, raw)
    except ValueError:
        return _coerce(defn, defn.default)


def validate(key: str, raw_value) -> str:
    """Validate and normalise a candidate value. Raises ValueError."""
    defn = BY_KEY.get(key)
    if defn is None:
        raise ValueError(f"Unknown setting: {key}")
    raw = ",".join(str(x) for x in raw_value) if isinstance(raw_value, list) else str(raw_value)
    if defn.type == "bool":
        if raw.strip().lower() not in ("true", "false", "1", "0", "yes", "no", "on", "off"):
            raise ValueError(f"{key} must be true or false")
        return "true" if raw.strip().lower() in ("true", "1", "yes", "on") else "false"
    try:
        parsed = _coerce(defn, raw)
    except ValueError:
        raise ValueError(f"{key} must be of type {defn.type}")
    numbers = parsed if isinstance(parsed, list) else [parsed]
    if defn.type in ("int", "float", "intlist"):
        if not numbers:
            raise ValueError(f"{key} needs at least one value")
        for n in numbers:
            if defn.min is not None and n < defn.min:
                raise ValueError(f"{key} must be >= {defn.min:g}")
            if defn.max is not None and n > defn.max:
                raise ValueError(f"{key} must be <= {defn.max:g}")
    return raw.strip()


def set_value(db: Session, key: str, raw_value, actor_id: uuid.UUID | None) -> SystemSetting:
    value = validate(key, raw_value)
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, value=value, updated_by=actor_id)
        db.add(row)
    else:
        row.value = value
        row.updated_by = actor_id
    db.flush()
    invalidate_cache()
    return row


def reset(db: Session, key: str) -> None:
    row = db.get(SystemSetting, key)
    if row is not None:
        db.delete(row)
        db.flush()
    invalidate_cache()


def describe_all(db: Session) -> list[dict]:
    stored = _stored(db)
    out = []
    for d in DEFINITIONS:
        out.append({
            "key": d.key,
            "category": d.category,
            "description": d.description,
            "type": d.type,
            "value": stored.get(d.key, d.default),
            "default": d.default,
            "is_default": d.key not in stored,
            "min": d.min,
            "max": d.max,
        })
    return out
