"""RTSA ITMS — FastAPI application entry point."""

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("RTSA ITMS starting up (env=%s)", settings.env)
    # Seed system notification templates on first boot
    try:
        from app.core.db import SessionLocal
        from app.services.notifications import seed_system_templates
        db = SessionLocal()
        try:
            seed_system_templates(db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Template seeding skipped: %s", exc)

    # Seed default system settings and thresholds
    try:
        from app.core.db import SessionLocal
        from app.scripts.seed_defaults import seed_defaults
        db = SessionLocal()
        try:
            seed_defaults(db)
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Default seed skipped: %s", exc)

    yield
    logger.info("RTSA ITMS shutting down.")


# ---------------------------------------------------------------------------
# App instance
# ---------------------------------------------------------------------------

app = FastAPI(
    title="RTSA Integrated Transport Management System",
    version="1.0.0",
    description="RTSA ITMS API — Developer 2: Citizen, Revenue, Integration, Security & Platform",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.env == "development" else ["https://itms.rtsa.gov.zm"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-Id", "X-Correlation-Id", "X-Response-Time-Ms"],
)

# ---------------------------------------------------------------------------
# Request ID / response-time middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
    correlation_id = request.headers.get("X-Correlation-Id") or request_id
    start = time.monotonic()

    response: Response = await call_next(request)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Correlation-Id"] = correlation_id
    response.headers["X-Response-Time-Ms"] = str(elapsed_ms)

    logger.debug(
        "%s %s → %d (%dms) req=%s",
        request.method, request.url.path, response.status_code, elapsed_ms, request_id,
    )
    return response

# ---------------------------------------------------------------------------
# Developer 1 routers (core transport domain — built here since Dev 1 delivered stubs)
# ---------------------------------------------------------------------------

from app.api.vehicles import router as vehicles_router      # noqa: E402
from app.api.drivers import router as drivers_router        # noqa: E402
from app.api.inspections import router as inspections_router # noqa: E402
from app.api.insurance import router as insurance_router    # noqa: E402
from app.api.violations import router as violations_router  # noqa: E402
from app.api.accidents import router as accidents_router    # noqa: E402
from app.api.anpr import router as anpr_router              # noqa: E402
from app.api.toll import router as toll_router              # noqa: E402
from app.api.psv import router as psv_router                # noqa: E402

for r in [
    vehicles_router,
    drivers_router,
    inspections_router,
    insurance_router,
    violations_router,
    accidents_router,
    anpr_router,
    toll_router,
    psv_router,
]:
    app.include_router(r)

# ---------------------------------------------------------------------------
# Developer 2 routers (citizen, revenue, integration, security & platform)
# ---------------------------------------------------------------------------

from app.api.auth import router as auth_router           # noqa: E402
from app.api.auth import rbac_router, security_router    # noqa: E402
from app.api.citizen import router as citizen_router     # noqa: E402
from app.api.citizen import app_router                   # noqa: E402
from app.api.payments import router as payments_router   # noqa: E402
from app.api.payments import revenue_router              # noqa: E402
from app.api.notifications import router as notif_router # noqa: E402
from app.api.admin import router as admin_router         # noqa: E402
from app.api.reports import router as reports_router     # noqa: E402
from app.api.inter_agency import router as ia_router     # noqa: E402

for r in [
    auth_router,
    rbac_router,
    security_router,
    citizen_router,
    app_router,
    payments_router,
    revenue_router,
    notif_router,
    admin_router,
    reports_router,
    ia_router,
]:
    app.include_router(r)

# ---------------------------------------------------------------------------
# Health endpoints (sections 18/19 — liveness + readiness)
# ---------------------------------------------------------------------------

@app.get("/v1/health", tags=["Platform"])
def health_liveness():
    """Liveness probe — no auth required."""
    return {"status": "ok", "service": "rtsa-itms"}


@app.get("/v1/health/ready", tags=["Platform"])
def health_readiness():
    """Readiness probe — checks DB connectivity."""
    from app.core.db import SessionLocal
    from sqlalchemy import text
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db.close()
        db_ok = True
    except Exception as exc:
        logger.error("DB readiness check failed: %s", exc)
        db_ok = False

    if not db_ok:
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": "unreachable"},
        )
    return {"status": "ready", "db": "ok"}


@app.get("/", tags=["Platform"])
def root():
    return {"status": "ok", "service": "rtsa-itms", "version": "1.0.0"}
