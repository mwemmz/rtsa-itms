from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission, user_has_permission
from app.core.security import get_current_user
from app.models.user import User
from app.services import exporters
from app.services import reports as svc
from app.services import settings as runtime_settings
from app.services.audit import log_action

router = APIRouter(prefix="/api/reports", tags=["Reports & Analytics"])


@router.get("/")
def catalogue(current_user: User = Depends(require_permission("reports:view"))):
    return [{"key": r.key, "title": r.title, "description": r.description, "columns": r.columns}
            for r in svc.REPORTS.values()]


@router.get("/dashboard")
def analytics_dashboard(
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports:view")),
):
    """Consolidated KPIs, monthly trends and breakdowns across every module."""
    start, end = svc.resolve_range(date_from, date_to, runtime_settings.get(db, "reports.default_range_days"))
    return svc.dashboard(db, start, end)


@router.get("/{key}")
def run_report(
    key: str,
    format: Literal["json", "csv", "xlsx", "pdf"] = "json",
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    limit: int = Query(500, ge=1, le=svc.MAX_EXPORT_ROWS),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("reports:view")),
):
    report = svc.REPORTS.get(key)
    if report is None:
        raise HTTPException(status_code=404, detail=f"Unknown report '{key}'")
    if format != "json" and not user_has_permission(db, current_user, "reports:export"):
        raise HTTPException(status_code=403, detail="Missing permission: reports:export")

    start, end = svc.resolve_range(date_from, date_to, runtime_settings.get(db, "reports.default_range_days"))
    if format != "json":
        limit = svc.MAX_EXPORT_ROWS if limit == 500 else limit
    rows = report.rows(db, start, end, limit)
    summary = report.summary(db, start, end)

    if format == "json":
        return {
            "key": report.key, "title": report.title,
            "range": {"from": start.isoformat(), "to": end.isoformat()},
            "columns": report.columns, "summary": summary, "count": len(rows),
            "rows": [[c.isoformat() if isinstance(c, datetime) else c for c in row] for row in rows],
        }

    log_action(db, "export_report", "report", key, f"{format} {start:%Y-%m-%d}..{end:%Y-%m-%d} ({len(rows)} rows)",
               current_user.id)
    db.commit()
    subtitle = f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"
    if format == "csv":
        body = exporters.to_csv(report.columns, rows)
    elif format == "xlsx":
        body = exporters.to_xlsx(report.title, report.columns, rows, summary)
    else:
        body = exporters.to_pdf(report.title, report.columns, rows, summary, subtitle)
    filename = f"rtsa-{report.key}-{datetime.utcnow():%Y%m%d}.{format}"
    return Response(body, media_type=exporters.MEDIA_TYPES[format],
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
