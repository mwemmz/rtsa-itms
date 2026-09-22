"""Cross-cutting ASGI middleware: security headers, HTTPS, timing, metrics,
request IDs and inter-agency call logging.

Written as a pure ASGI middleware (no BaseHTTPMiddleware) so it adds
microseconds, not milliseconds, to every request.
"""

import time
import uuid

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse, RedirectResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core import metrics
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger("http")

# The single-page app is same-origin only; Swagger UI (/docs) loads from a CDN
# so it is exempt from the strict policy.
_CSP_PATHS = {"/", "/app"}
_CSP = (
    "default-src 'self'; script-src 'self' https://unpkg.com; "
    "style-src 'self' 'unsafe-inline' https://unpkg.com; "
    "img-src 'self' data: https://*.tile.openstreetmap.org https://tile.openstreetmap.org https://*.basemaps.cartocdn.com https://basemaps.cartocdn.com; font-src 'self'; "
    "connect-src 'self' https://unpkg.com; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)
_HEALTH_PATHS = {"/health", "/health/live", "/health/ready"}


class PlatformMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        path = scope["path"]

        if settings.FORCE_HTTPS and path not in _HEALTH_PATHS:
            proto = headers.get("x-forwarded-proto", scope.get("scheme", "http"))
            if proto == "http":
                url = "https://" + headers.get("host", "") + path
                if scope.get("query_string"):
                    url += "?" + scope["query_string"].decode()
                await RedirectResponse(url, status_code=308)(scope, receive, send)
                return

        request_id = headers.get("x-request-id") or uuid.uuid4().hex[:16]
        start = time.perf_counter()
        status_holder = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["code"] = message["status"]
                elapsed = (time.perf_counter() - start) * 1000
                h = MutableHeaders(scope=message)
                h["X-Request-ID"] = request_id
                h["X-Process-Time"] = f"{elapsed:.1f}ms"
                h["Server-Timing"] = f"app;dur={elapsed:.1f}"
                h["X-Content-Type-Options"] = "nosniff"
                h["X-Frame-Options"] = "DENY"
                h["Referrer-Policy"] = "strict-origin-when-cross-origin"
                h["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
                if path.startswith("/api/"):
                    h["Cache-Control"] = "no-store"
                if path in _CSP_PATHS:
                    h["Content-Security-Policy"] = _CSP
                if settings.ENVIRONMENT == "production" or settings.FORCE_HTTPS:
                    h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start) * 1000
            route = scope.get("route")
            route_path = getattr(route, "path", None) or ("/static" if path.startswith("/static") else path)
            key = f"{scope['method']} {route_path}"
            metrics.record(key, elapsed_ms, status_holder["code"], settings.SLOW_REQUEST_MS)
            if elapsed_ms > settings.SLOW_REQUEST_MS:
                logger.warning("slow request %s %s took %.0fms (id=%s)", scope["method"], route_path, elapsed_ms, request_id)
            agency = scope.get("state", {}).get("agency") if isinstance(scope.get("state"), dict) else None
            if agency is not None:
                _log_agency_call(agency, route_path, status_holder["code"], elapsed_ms)


def _log_agency_call(agency: dict, endpoint: str, status_code: int, elapsed_ms: float) -> None:
    from app.core.database import SessionLocal
    from app.models.platform import IntegrationLog

    db = SessionLocal()
    try:
        db.add(IntegrationLog(
            agency_id=agency["id"], agency_name=agency["name"], direction="inbound", endpoint=endpoint[:200],
            status_code=status_code, success=status_code < 400, latency_ms=round(elapsed_ms, 2),
            detail=agency.get("detail"),
        ))
        db.commit()
    except Exception as exc:  # monitoring must never break the request
        logger.error("could not record integration log: %s", exc)
    finally:
        db.close()
