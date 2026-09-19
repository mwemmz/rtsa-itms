"""Operations endpoints: performance metrics, backups and DR status."""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core import metrics
from app.core.config import settings
from app.core.database import engine, get_db
from app.core.permissions import require_permission
from app.models.audit_log import AuditLog
from app.models.driver import Driver
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.toll import TollTransaction
from app.models.user import User
from app.models.vehicle import Vehicle
from app.services import settings as runtime_settings
from app.services.audit import log_action

router = APIRouter(prefix="/api/system", tags=["System & Operations"])

TOLL_ROUTE = "POST /api/toll/events"


@router.get("/metrics")
def performance_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system:monitor")),
):
    """Request latency percentiles per route, plus how the toll decision path is doing
    against its response-time target."""
    snap = metrics.snapshot()
    target = runtime_settings.get(db, "toll.compliance_target_ms")
    toll = snap["routes"].get(TOLL_ROUTE)
    snap["toll_compliance_target"] = {
        "target_ms": target,
        "p95_ms": toll["p95_ms"] if toll else None,
        "within_target": (toll["p95_ms"] <= target) if toll else None,
        "samples": toll["requests"] if toll else 0,
    }
    pool = engine.pool
    snap["database"] = {
        "dialect": engine.dialect.name,
        "pool_size": getattr(pool, "size", lambda: None)() if hasattr(pool, "size") else None,
        "checked_out": getattr(pool, "checkedout", lambda: None)() if hasattr(pool, "checkedout") else None,
    }
    return snap


@router.get("/capacity")
def capacity(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system:monitor")),
):
    """Table sizes: the numbers to watch when planning for millions of records."""
    counts = {
        "vehicles": db.query(func.count(Vehicle.id)).scalar(),
        "drivers": db.query(func.count(Driver.id)).scalar(),
        "toll_transactions": db.query(func.count(TollTransaction.id)).scalar(),
        "payments": db.query(func.count(Payment.id)).scalar(),
        "notifications": db.query(func.count(Notification.id)).scalar(),
        "audit_logs": db.query(func.count(AuditLog.id)).scalar(),
    }
    out = {"row_counts": counts, "database_bytes": None}
    if engine.dialect.name == "postgresql":
        out["database_bytes"] = db.execute(text("SELECT pg_database_size(current_database())")).scalar()
    return out


@router.get("/backups")
def backups(current_user: User = Depends(require_permission("system:monitor"))):
    from scripts.backup import list_backups

    items = list_backups()
    return {"directory": settings.BACKUP_DIR, "retention": settings.BACKUP_RETENTION, "count": len(items),
            "latest": items[0] if items else None, "backups": items}


@router.post("/backups")
def create_backup(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("system:monitor")),
):
    from scripts.backup import run_backup, verify_backup

    try:
        path = run_backup()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Backup failed: {exc}")
    result = verify_backup(Path(path))
    log_action(db, "backup", "system", path.name, f"verified={result.get('ok')}", current_user.id)
    db.commit()
    return {"file": path.name, "size_bytes": path.stat().st_size, "verification": result}


@router.post("/expiry-scan")
def run_expiry_scan(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    """Run the expiry-reminder scan now (the worker also runs it hourly)."""
    from app.services.expiry import scan_expiries

    sent = scan_expiries(db)
    log_action(db, "expiry_scan", "system", None, str(sent), current_user.id)
    db.commit()
    return {"sent": sent}


@router.post("/dispatch-notifications")
def dispatch_now(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("notifications:manage")),
):
    """Deliver queued SMS/email now instead of waiting for the worker's next poll."""
    from app.services.delivery import dispatch_pending

    return dispatch_pending(db)
