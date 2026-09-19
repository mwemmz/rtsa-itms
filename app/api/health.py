import time

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.database import get_db

router = APIRouter()


def _db_check(db: Session) -> tuple[bool, str, float]:
    start = time.perf_counter()
    try:
        db.execute(text("SELECT 1"))
        return True, "connected", (time.perf_counter() - start) * 1000
    except Exception as e:
        return False, f"error: {e}", (time.perf_counter() - start) * 1000


@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    ok, db_status, _ = _db_check(db)
    return {"status": "healthy", "database": db_status, "service": "RTSA ITMS"}


@router.get("/health/live")
def liveness():
    """Process is up. Used by the platform to decide whether to restart the container."""
    return {"status": "alive"}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)):
    """Ready to take traffic: the database answers. Returns 503 otherwise so a load
    balancer stops routing here until it recovers."""
    ok, db_status, ms = _db_check(db)
    if not ok:
        response.status_code = 503
    return {"status": "ready" if ok else "unavailable", "database": db_status, "db_latency_ms": round(ms, 2)}
