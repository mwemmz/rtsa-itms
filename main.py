from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

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
from app.api.health import router as health_router
from app.api import incidents, portal, road_network, routing
from app.core.bootstrap import run_startup_tasks
from app.core.config import settings
from app.ui import landing_page


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.RUN_MIGRATIONS_ON_STARTUP:
        run_startup_tasks()
    yield


app = FastAPI(
    title="RTSA Integrated Transport Management System",
    description="Backend API for vehicle registration, driver licensing, enforcement, toll compliance, road alerts and routing, and more.",
    version="0.8.0",
    lifespan=lifespan,
)

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


@app.get("/", response_class=HTMLResponse)
def root():
    return landing_page()