"""Reporting & Analytics — section 16."""

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import require_roles
from app.models.admin import ActorType, ExportJobStatus, ReportExportJob
from app.models.citizen import Application, ApplicationStatus, User, UserRole
from app.models.payments import PaymentStatus, PaymentTransaction, PaymentType
from app.schemas.admin import ExportJobCreate, ExportJobOut
from app.schemas.reports import DashboardKPI, ReportDataResponse, ReportDefinition

router = APIRouter(prefix="/v1/reports", tags=["Reports & Analytics"])

_ALLOWED_ROLES = (UserRole.ADMIN, UserRole.AUDITOR, UserRole.OFFICER)

REPORT_DEFINITIONS = [
    ReportDefinition(
        report_type="revenue-by-type",
        display_name="Revenue by Payment Type",
        description="Total settled revenue broken down by payment type over a date range.",
        available_filters=["dateFrom", "dateTo", "currency"],
        required_permission="REPORT.REVENUE.READ",
    ),
    ReportDefinition(
        report_type="revenue-by-period",
        display_name="Revenue by Period",
        description="Daily/monthly revenue aggregation.",
        available_filters=["dateFrom", "dateTo", "groupBy"],
        required_permission="REPORT.REVENUE.READ",
    ),
    ReportDefinition(
        report_type="applications-summary",
        display_name="Applications Summary",
        description="Citizen application volumes and statuses.",
        available_filters=["dateFrom", "dateTo", "status", "applicationType"],
        required_permission="REPORT.APPLICATIONS.READ",
    ),
    ReportDefinition(
        report_type="violations-summary",
        display_name="Violations Summary",
        description="Violation counts, types and fine revenue by status.",
        available_filters=["dateFrom", "dateTo", "officerId"],
        required_permission="REPORT.VIOLATIONS.READ",
    ),
    ReportDefinition(
        report_type="accidents-summary",
        display_name="Accidents Summary",
        description="Accident counts by severity with fatality and injury totals.",
        available_filters=["dateFrom", "dateTo"],
        required_permission="REPORT.ACCIDENTS.READ",
    ),
    ReportDefinition(
        report_type="toll-summary",
        display_name="Toll Enforcement Summary",
        description="Toll transit counts and revenue by payment status.",
        available_filters=["dateFrom", "dateTo", "plazaId"],
        required_permission="REPORT.TOLL.READ",
    ),
    ReportDefinition(
        report_type="user-activity",
        display_name="User & Authentication Activity",
        description="Login events, MFA usage and account lockouts.",
        available_filters=["dateFrom", "dateTo", "role"],
        required_permission="REPORT.SECURITY.READ",
    ),
    ReportDefinition(
        report_type="notification-delivery",
        display_name="Notification Delivery Report",
        description="Notification volumes, delivery success rates and channel breakdown.",
        available_filters=["dateFrom", "dateTo", "channel", "templateKey"],
        required_permission="REPORT.NOTIFICATIONS.READ",
    ),
    ReportDefinition(
        report_type="integration-health",
        display_name="Inter-Agency Integration Health",
        description="Call volumes, latencies and error rates per external agency.",
        available_filters=["dateFrom", "dateTo", "agency"],
        required_permission="REPORT.INTEGRATIONS.READ",
    ),
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Dashboard KPIs
# ---------------------------------------------------------------------------

@router.get("/dashboard", response_model=DashboardKPI)
def dashboard(
    current_user: User = Depends(require_roles(*_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    from app.models.vehicles import Vehicle
    from app.models.drivers import Driver
    from app.models.violations import Violation, ViolationStatus
    from app.models.accidents import Accident
    from app.models.psv import PSVPermit, PSVPermitStatus
    from app.models.toll import TollRecord, TollPaymentStatus

    today_start = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    year_start = _utcnow().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    total_vehicles = db.query(func.count(Vehicle.id)).scalar() or 0
    total_drivers = db.query(func.count(Driver.id)).scalar() or 0

    total_violations_ytd = (
        db.query(func.count(Violation.id))
        .filter(Violation.created_at >= year_start)
        .scalar() or 0
    )
    total_accidents_ytd = (
        db.query(func.count(Accident.id))
        .filter(Accident.occurred_at >= year_start)
        .scalar() or 0
    )
    revenue_row = (
        db.query(func.coalesce(func.sum(PaymentTransaction.amount_ngwee), 0))
        .filter(PaymentTransaction.status == PaymentStatus.SETTLED)
        .scalar() or 0
    )
    pending_apps = (
        db.query(func.count(Application.id))
        .filter(Application.status.in_([
            ApplicationStatus.SUBMITTED,
            ApplicationStatus.UNDER_REVIEW,
            ApplicationStatus.ACTION_REQUIRED,
        ]))
        .scalar() or 0
    )
    active_psv = (
        db.query(func.count(PSVPermit.id))
        .filter(PSVPermit.status == PSVPermitStatus.ACTIVE)
        .scalar() or 0
    )
    toll_today = (
        db.query(func.count(TollRecord.id))
        .filter(TollRecord.transited_at >= today_start)
        .scalar() or 0
    )

    return DashboardKPI(
        total_registered_vehicles=total_vehicles,
        total_licensed_drivers=total_drivers,
        total_violations_ytd=total_violations_ytd,
        total_accidents_ytd=total_accidents_ytd,
        revenue_collected_ngwee=int(revenue_row),
        pending_applications=pending_apps,
        active_psv_vehicles=active_psv,
        toll_transactions_today=toll_today,
    )


# ---------------------------------------------------------------------------
# Report definitions
# ---------------------------------------------------------------------------

@router.get("/definitions", response_model=list[ReportDefinition])
def list_definitions(
    current_user: User = Depends(require_roles(*_ALLOWED_ROLES)),
):
    return REPORT_DEFINITIONS


# ---------------------------------------------------------------------------
# Report data (parameterized)
# ---------------------------------------------------------------------------

@router.get("/{report_type}", response_model=ReportDataResponse)
def get_report(
    report_type: str,
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    current_user: User = Depends(require_roles(*_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    defn = next((d for d in REPORT_DEFINITIONS if d.report_type == report_type), None)
    if not defn:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Report type '{report_type}' not found."})

    filters_applied = {"dateFrom": date_from, "dateTo": date_to}
    columns: list[str] = []
    rows: list[dict] = []

    if report_type == "revenue-by-type":
        columns = ["payment_type", "total_ngwee", "transaction_count"]
        q = db.query(
            PaymentTransaction.payment_type,
            func.sum(PaymentTransaction.amount_ngwee).label("total_ngwee"),
            func.count(PaymentTransaction.id).label("count"),
        ).filter(PaymentTransaction.status == PaymentStatus.SETTLED)
        if date_from:
            q = q.filter(PaymentTransaction.settled_at >= date_from)
        if date_to:
            q = q.filter(PaymentTransaction.settled_at <= date_to)
        q = q.group_by(PaymentTransaction.payment_type)
        for row in q.all():
            rows.append({"payment_type": row[0].value, "total_ngwee": int(row[1] or 0), "transaction_count": row[2]})

    elif report_type == "revenue-by-period":
        columns = ["date", "total_ngwee", "transaction_count"]
        q = db.query(
            func.date_trunc("day", PaymentTransaction.settled_at).label("day"),
            func.sum(PaymentTransaction.amount_ngwee).label("total"),
            func.count(PaymentTransaction.id).label("count"),
        ).filter(PaymentTransaction.status == PaymentStatus.SETTLED)
        if date_from:
            q = q.filter(PaymentTransaction.settled_at >= date_from)
        if date_to:
            q = q.filter(PaymentTransaction.settled_at <= date_to)
        q = q.group_by("day").order_by("day")
        for row in q.all():
            rows.append({"date": str(row[0]), "total_ngwee": int(row[1] or 0), "transaction_count": row[2]})

    elif report_type == "applications-summary":
        columns = ["status", "count"]
        q = db.query(Application.status, func.count(Application.id).label("count"))
        if date_from:
            q = q.filter(Application.created_at >= date_from)
        if date_to:
            q = q.filter(Application.created_at <= date_to)
        q = q.group_by(Application.status)
        for row in q.all():
            rows.append({"status": row[0].value, "count": row[1]})

    elif report_type == "user-activity":
        columns = ["role", "total_users", "active_users"]
        for role in UserRole:
            total = db.query(func.count(User.id)).filter(User.role == role).scalar() or 0
            active = db.query(func.count(User.id)).filter(User.role == role, User.is_active == True).scalar() or 0  # noqa: E712
            if total > 0:
                rows.append({"role": role.value, "total_users": total, "active_users": active})

    elif report_type == "notification-delivery":
        from app.models.notifications import Notification, NotificationStatus
        columns = ["channel", "status", "count"]
        q = db.query(
            Notification.channel,
            Notification.status,
            func.count(Notification.id).label("count"),
        )
        if date_from:
            q = q.filter(Notification.created_at >= date_from)
        if date_to:
            q = q.filter(Notification.created_at <= date_to)
        q = q.group_by(Notification.channel, Notification.status)
        for row in q.all():
            rows.append({"channel": row[0].value, "status": row[1].value, "count": row[2]})

    elif report_type == "integration-health":
        from app.models.admin import IntegrationLog
        columns = ["agency", "direction", "total_calls", "success_count", "error_count", "avg_latency_ms"]
        q = db.query(
            IntegrationLog.agency,
            IntegrationLog.direction,
            func.count(IntegrationLog.id).label("total"),
            func.sum(func.cast(IntegrationLog.success, db.bind.dialect.name == "postgresql" and "integer" or "integer")).label("successes"),
            func.avg(IntegrationLog.latency_ms).label("avg_latency"),
        )
        if date_from:
            q = q.filter(IntegrationLog.occurred_at >= date_from)
        if date_to:
            q = q.filter(IntegrationLog.occurred_at <= date_to)
        q = q.group_by(IntegrationLog.agency, IntegrationLog.direction)
        for row in q.all():
            rows.append({
                "agency": row[0],
                "direction": row[1],
                "total_calls": row[2],
                "success_count": int(row[3] or 0),
                "error_count": row[2] - int(row[3] or 0),
                "avg_latency_ms": round(float(row[4] or 0), 1),
            })

    elif report_type == "violations-summary":
        from app.models.violations import Violation, ViolationStatus, ViolationType
        columns = ["violation_type", "status", "count", "total_fines_ngwee"]
        q = db.query(
            Violation.violation_type,
            Violation.status,
            func.count(Violation.id).label("count"),
            func.coalesce(func.sum(Violation.fine_amount_ngwee), 0).label("total"),
        )
        if date_from:
            q = q.filter(Violation.occurred_at >= date_from)
        if date_to:
            q = q.filter(Violation.occurred_at <= date_to)
        q = q.group_by(Violation.violation_type, Violation.status)
        for row in q.all():
            rows.append({
                "violation_type": row[0].value,
                "status": row[1].value,
                "count": row[2],
                "total_fines_ngwee": int(row[3]),
            })

    elif report_type == "accidents-summary":
        from app.models.accidents import Accident, AccidentSeverity
        columns = ["severity", "count", "total_fatalities", "total_injuries"]
        q = db.query(
            Accident.severity,
            func.count(Accident.id).label("count"),
            func.coalesce(func.sum(Accident.fatalities), 0).label("fatalities"),
            func.coalesce(func.sum(Accident.injuries), 0).label("injuries"),
        )
        if date_from:
            q = q.filter(Accident.occurred_at >= date_from)
        if date_to:
            q = q.filter(Accident.occurred_at <= date_to)
        q = q.group_by(Accident.severity)
        for row in q.all():
            rows.append({
                "severity": row[0].value,
                "count": row[1],
                "total_fatalities": int(row[2]),
                "total_injuries": int(row[3]),
            })

    elif report_type == "toll-summary":
        from app.models.toll import TollRecord, TollPaymentStatus
        columns = ["payment_status", "count", "total_amount_ngwee"]
        q = db.query(
            TollRecord.payment_status,
            func.count(TollRecord.id).label("count"),
            func.coalesce(func.sum(TollRecord.amount_ngwee), 0).label("total"),
        )
        if date_from:
            q = q.filter(TollRecord.transited_at >= date_from)
        if date_to:
            q = q.filter(TollRecord.transited_at <= date_to)
        q = q.group_by(TollRecord.payment_status)
        for row in q.all():
            rows.append({
                "payment_status": row[0].value,
                "count": row[1],
                "total_amount_ngwee": int(row[2]),
            })

    return ReportDataResponse(
        report_type=report_type,
        generated_at=_utcnow(),
        filters_applied=filters_applied,
        columns=columns,
        rows=rows,
        total_rows=len(rows),
    )


# ---------------------------------------------------------------------------
# Export jobs (async for large reports)
# ---------------------------------------------------------------------------

@router.post("/{report_type}/exports", response_model=ExportJobOut, status_code=202)
def create_export(
    report_type: str,
    body: ExportJobCreate,
    current_user: User = Depends(require_roles(*_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    defn = next((d for d in REPORT_DEFINITIONS if d.report_type == report_type), None)
    if not defn:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": f"Report type '{report_type}' not found."})

    job = ReportExportJob(
        report_type=report_type,
        format=body.format,
        filters=json.dumps(body.filters),
        requested_by=current_user.id,
        status=ExportJobStatus.QUEUED,
    )
    db.add(job)
    db.flush()

    log_event(db, action="REPORT.EXPORT.REQUESTED", actor_id=current_user.id,
              actor_type=ActorType.ADMIN, resource_type="REPORT_EXPORT_JOB", resource_id=job.id,
              after={"report_type": report_type, "format": body.format})

    # Synchronously generate small reports; queue large ones
    # For the demo we generate all synchronously
    _run_export_job(db, job)

    db.commit()
    db.refresh(job)
    return ExportJobOut.model_validate(job)


@router.get("/exports/{job_id}", response_model=ExportJobOut)
def get_export_job(
    job_id: str,
    current_user: User = Depends(require_roles(*_ALLOWED_ROLES)),
    db: Session = Depends(get_db),
):
    job = db.get(ReportExportJob, job_id)
    if not job:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Export job not found."})
    return ExportJobOut.model_validate(job)


# ---------------------------------------------------------------------------
# Internal: run export synchronously (stub — real implementation uses Celery/background tasks)
# ---------------------------------------------------------------------------

def _run_export_job(db: Session, job: ReportExportJob) -> None:
    """Stub export runner — marks job COMPLETED with a placeholder download URL."""
    job.status = ExportJobStatus.RUNNING
    job.started_at = _utcnow()
    db.flush()

    try:
        # TODO: generate actual PDF/XLSX/CSV using openpyxl / weasyprint / csv module
        # For now produce a signed stub URL
        job.status = ExportJobStatus.COMPLETED
        job.row_count = 0
        job.download_url = f"https://storage.itms.rtsa.gov.zm/exports/{job.id}.{job.format.lower()}"
        job.url_expires_at = _utcnow() + timedelta(hours=1)
        job.completed_at = _utcnow()
    except Exception as exc:
        job.status = ExportJobStatus.FAILED
        job.error_message = str(exc)
        job.completed_at = _utcnow()

    db.flush()
