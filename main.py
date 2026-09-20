from contextlib import asynccontextmanager
import mimetypes

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import StatementError

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")

from app.api import (
    accidents,
    admin,
    anpr,
    auth,
    citizen,
    drivers,
    enforcement,
    inspections,
    insurance,
    licence,
    notifications,
    payments,
    psv,
    toll,
    vehicles,
)
from app.api import integration, lookup, reports, system
from app.api.health import router as health_router
from app.api import incidents, portal, road_network, routing
from app.core.bootstrap import run_startup_tasks
from app.core.config import settings
from app.core.middleware import PlatformMiddleware
from app.ui import landing_page


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_startup_tasks()
    yield


app = FastAPI(
    title="RTSA Integrated Transport Management System",
    description="Backend API for vehicle registration, driver licensing, enforcement, toll compliance, road alerts and routing, and more.",
    version="0.9.0",
    lifespan=lifespan,
)

@app.exception_handler(StatementError)
async def malformed_identifier(_, exc: StatementError):
    """A path/query id that isn't a valid UUID can't match any row: answer 404, not 500."""
    if isinstance(exc.orig, ValueError) and "UUID" in str(exc.orig):
        return JSONResponse(status_code=404, content={"detail": "Not found"})
    import logging

    logging.getLogger("http").error("Database error: %s", exc, exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(health_router, tags=["Health"])
app.include_router(auth.router)
app.include_router(vehicles.router)
app.include_router(drivers.router)
app.include_router(inspections.router)
app.include_router(insurance.router)
app.include_router(licence.router)
app.include_router(enforcement.router)
app.include_router(anpr.router)
app.include_router(toll.router)
app.include_router(psv.router)
app.include_router(accidents.router)
app.include_router(payments.router)
app.include_router(citizen.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(road_network.router)
app.include_router(incidents.router)
app.include_router(routing.router)
app.include_router(portal.router)
app.include_router(reports.router)
app.include_router(lookup.router)
app.include_router(integration.router)
app.include_router(system.router)

# Middleware runs bottom-up: gzip is innermost, the platform middleware
# (HTTPS redirect, security headers, timing, metrics) is outermost.
app.add_middleware(GZipMiddleware, minimum_size=1024)
if settings.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Device-Id"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
    )
app.add_middleware(PlatformMiddleware)

app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/", response_class=HTMLResponse)
def root():
    return FileResponse("app/static/app.html")


@app.get("/app", response_class=HTMLResponse)
def web_app():
    return FileResponse("app/static/app.html")


@app.get("/about", response_class=HTMLResponse)
def about():
    return landing_page()