"""Inter-agency integration: API-key authentication, scopes, throttling and
outbound adapters.

Each external agency (police, insurers, hospitals, toll authorities, the
national ID system) is registered as an ``AgencyClient`` with an API key, a set
of scopes (its *data-sharing contract*) and optional contract expiry. Every
call — inbound or outbound — is written to ``integration_logs`` so the
integration can be monitored.
"""

import re
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

import httpx
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Integer, cast, func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import constant_time_equals, sha256
from app.core.database import get_db
from app.core.timeutil import aware, utcnow
from app.models.platform import AgencyClient, AgencyType, IntegrationLog

# What each agency type may be granted. A contract can only ever narrow this.
SCOPES_BY_TYPE: dict[str, set[str]] = {
    AgencyType.POLICE.value: {"vehicles:read", "drivers:read", "accidents:write"},
    AgencyType.INSURANCE.value: {"insurance:read", "insurance:write"},
    AgencyType.HOSPITAL.value: {"accidents:write"},
    AgencyType.TOLL_AUTHORITY.value: {"compliance:read", "vehicles:read"},
    AgencyType.NATIONAL_ID.value: {"identity:read"},
}
ALL_SCOPES = sorted(set().union(*SCOPES_BY_TYPE.values()))

_lock = threading.Lock()
_windows: dict[str, deque] = defaultdict(deque)


def generate_key() -> tuple[str, str, str]:
    """Return (full_key, prefix, sha256_hash). The full key is shown once."""
    prefix = secrets.token_hex(4)
    secret = secrets.token_urlsafe(32)
    full = f"rtsa_{prefix}_{secret}"
    return full, prefix, sha256(full)


def validate_scopes(agency_type: str, scopes: list[str]) -> list[str]:
    allowed = SCOPES_BY_TYPE.get(agency_type)
    if allowed is None:
        raise HTTPException(422,
                            f"agency_type must be one of {sorted(SCOPES_BY_TYPE)}")
    bad = sorted(set(scopes) - allowed)
    if bad:
        raise HTTPException(422,
                            f"Scopes not permitted for {agency_type}: {bad}. Allowed: {sorted(allowed)}")
    return sorted(set(scopes))


def _throttle(agency: AgencyClient) -> None:
    now = time.monotonic()
    with _lock:
        dq = _windows[str(agency.id)]
        while dq and now - dq[0] > 60:
            dq.popleft()
        if len(dq) >= agency.rate_limit_per_minute:
            retry = int(60 - (now - dq[0])) + 1
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Agency rate limit exceeded",
                                headers={"Retry-After": str(retry)})
        dq.append(now)


def reset_throttle() -> None:
    with _lock:
        _windows.clear()


def agency_auth(scope: str):
    """Dependency: authenticate an agency by ``X-API-Key`` and require ``scope``."""

    def dependency(
        request: Request,
        x_api_key: str | None = Header(None),
        db: Session = Depends(get_db),
    ) -> AgencyClient:
        if not x_api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
        parts = x_api_key.split("_", 2)
        agency = None
        if len(parts) == 3 and parts[0] == "rtsa":
            agency = db.query(AgencyClient).filter(AgencyClient.api_key_prefix == parts[1]).first()
        if agency is None or not constant_time_equals(agency.api_key_hash, sha256(x_api_key)):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid API key")

        # From here on the caller is known, so failures are attributed and logged.
        request.state.agency = {"id": agency.id, "name": agency.name}
        if not agency.is_active:
            request.state.agency["detail"] = "agency disabled"
            raise HTTPException(status.HTTP_403_FORBIDDEN, "This API client is disabled")
        if agency.contract_expires_at and aware(agency.contract_expires_at) < utcnow():
            request.state.agency["detail"] = "data-sharing contract expired"
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Data-sharing agreement has expired")
        if scope not in agency.scopes.split(","):
            request.state.agency["detail"] = f"missing scope {scope}"
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Your data-sharing contract does not include '{scope}'")
        _throttle(agency)
        # Throttle by touch: only write last_used occasionally to avoid a write per call.
        last = aware(agency.last_used_at)
        if last is None or (utcnow() - last).total_seconds() > 60:
            agency.last_used_at = utcnow()
            db.commit()
        return agency

    return dependency


# --- outbound: national ID verification --------------------------------------------------

NRC_PATTERN = re.compile(r"^\d{6}/\d{2}/\d$")


def _log_outbound(db: Session, name: str, endpoint: str, code: int, ms: float, detail: str | None) -> None:
    db.add(IntegrationLog(agency_name=name, direction="outbound", endpoint=endpoint, status_code=code,
                          success=code < 400, latency_ms=round(ms, 2), detail=detail))
    db.commit()


def verify_national_id(db: Session, nrc: str) -> dict:
    """Verify a National Registration Card number.

    With ``NATIONAL_ID_API_URL`` configured the request is forwarded to the
    registry. Otherwise a **sandbox** result is returned: the number is only
    format-checked and the response says so explicitly, so nobody mistakes it
    for a real identity check.
    """
    nrc = nrc.strip()
    if not NRC_PATTERN.match(nrc):
        raise HTTPException(422, "NRC must look like 123456/78/1")
    start = time.perf_counter()
    url = settings.NATIONAL_ID_API_URL
    if not url:
        result = {"nrc": nrc, "format_valid": True, "verified": False, "source": "sandbox",
                  "note": "No national ID registry configured; only the number format was checked."}
        _log_outbound(db, "National ID (sandbox)", "verify", 200, (time.perf_counter() - start) * 1000, "format check")
        return result
    try:
        resp = httpx.get(url, params={"nrc": nrc}, timeout=8,
                         headers={"Authorization": f"Bearer {settings.NATIONAL_ID_API_TOKEN}"})
        _log_outbound(db, "National ID", "verify", resp.status_code, (time.perf_counter() - start) * 1000, None)
        resp.raise_for_status()
        data = resp.json()
        return {"nrc": nrc, "format_valid": True, "verified": bool(data.get("verified")),
                "source": "national_id_registry", "full_name": data.get("full_name")}
    except httpx.HTTPError as exc:
        _log_outbound(db, "National ID", "verify", 502, (time.perf_counter() - start) * 1000, str(exc)[:200])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "National ID registry unavailable")


# --- monitoring ---------------------------------------------------------------------------------

def monitoring_summary(db: Session, hours: int = 24) -> list[dict]:
    since = utcnow() - timedelta(hours=hours)
    rows = (
        db.query(
            IntegrationLog.agency_name, IntegrationLog.direction,
            func.count(), func.sum(cast(IntegrationLog.success, Integer)),
            func.avg(IntegrationLog.latency_ms), func.max(IntegrationLog.latency_ms),
            func.max(IntegrationLog.created_at),
        )
        .filter(IntegrationLog.created_at >= since)
        .group_by(IntegrationLog.agency_name, IntegrationLog.direction)
        .all()
    )
    out = []
    for name, direction, total, ok, avg, mx, last in rows:
        ok = int(ok or 0)
        out.append({
            "agency": name, "direction": direction, "calls": total, "successes": ok, "failures": total - ok,
            "success_rate": round(ok / total * 100, 1) if total else None,
            "avg_latency_ms": round(float(avg or 0), 1), "max_latency_ms": round(float(mx or 0), 1),
            "last_call_at": last.isoformat() if last else None,
        })
    return sorted(out, key=lambda r: -r["calls"])

