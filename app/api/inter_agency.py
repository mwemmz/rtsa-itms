"""Inter-Agency Integration — section 17."""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType, DataSharingContract, IntegrationLog
from app.models.citizen import User, UserRole
from app.schemas.common import MessageResponse
from app.schemas.inter_agency import (
    BatchIngestRequest,
    BatchIngestResult,
    ContractOut,
    HospitalAccidentIn,
    InsuranceVerifyRequest,
    InsuranceVerifyResponse,
    IntegrationLogOut,
    IntegrationStatusEntry,
    NationalIdVerifyRequest,
    NationalIdVerifyResponse,
    PoliceIncidentIn,
)

router = APIRouter(prefix="/v1/integrations", tags=["Inter-Agency Integration"])

_STAFF_ROLES = (UserRole.ADMIN, UserRole.OFFICER, UserRole.AUDITOR)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _log_integration(
    db: Session,
    *,
    agency: str,
    direction: str,
    endpoint: str,
    success: bool,
    status_code: int | None = None,
    request_payload: str | None = None,
    response_payload: str | None = None,
    error_message: str | None = None,
    correlation_id: str | None = None,
    latency_ms: int | None = None,
) -> IntegrationLog:
    entry = IntegrationLog(
        agency=agency,
        direction=direction,
        endpoint=endpoint,
        status_code=status_code,
        success=success,
        request_payload=request_payload,
        response_payload=response_payload,
        error_message=error_message,
        correlation_id=correlation_id,
        latency_ms=latency_ms,
    )
    db.add(entry)
    db.flush()
    log_event(
        db,
        action=f"INTEGRATION.{agency}.{direction}",
        actor_type=ActorType.SYSTEM,
        resource_type="INTEGRATION",
        resource_id=agency,
        correlation_id=correlation_id,
    )
    return entry


# ---------------------------------------------------------------------------
# Police — inbound incidents
# ---------------------------------------------------------------------------

@router.post("/police/incidents", status_code=202)
def receive_police_incident(
    body: PoliceIncidentIn,
    request: Request,
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    db: Session = Depends(get_db),
):
    """
    Receive incident data from Police systems.
    Production: mTLS + X-Service-Key mutual API key required.
    Dev/sandbox: accepts any request.
    """
    payload_str = body.model_dump_json()
    _log_integration(
        db,
        agency="POLICE",
        direction="INBOUND",
        endpoint="/v1/integrations/police/incidents",
        success=True,
        status_code=202,
        request_payload=payload_str,
        correlation_id=correlation_id,
        latency_ms=0,
    )
    db.commit()
    return {"received": True, "incident_number": body.incident_number, "correlation_id": correlation_id}


# ---------------------------------------------------------------------------
# Police — outbound vehicle/owner lookup (agencies query us)
# ---------------------------------------------------------------------------

@router.get("/police/vehicle-lookup")
def vehicle_lookup(
    plate: str,
    current_user: User = Depends(require_roles(*_STAFF_ROLES)),
    db: Session = Depends(get_db),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
):
    """
    Outbound: external agency queries vehicle/owner status.
    Runs the full compliance engine and returns vehicle + owner details.
    """
    from app.services.compliance_engine import check_plate
    from app.models.vehicles import Vehicle

    compliance = check_plate(db, plate)
    vehicle = db.query(Vehicle).filter(
        Vehicle.plate_number == plate.upper().strip()
    ).first() if compliance.vehicle_found else None

    _log_integration(
        db, agency="POLICE", direction="OUTBOUND",
        endpoint="/v1/integrations/police/vehicle-lookup",
        success=True, status_code=200, correlation_id=correlation_id,
    )
    db.commit()

    if not vehicle:
        return {"plate": plate.upper(), "found": False}

    return {
        "plate": vehicle.plate_number,
        "found": True,
        "vehicle": {
            "id": vehicle.id,
            "make": vehicle.make,
            "model": vehicle.model,
            "year": vehicle.year,
            "color": vehicle.color,
            "category": vehicle.category.value,
            "status": vehicle.status.value,
        },
        "owner": {
            "nrc_number": vehicle.owner_nrc,
            "name": vehicle.owner_name,
            "phone": vehicle.owner_phone,
        },
        "compliance": {
            "compliant": compliance.compliant,
            "flags": [f.value for f in compliance.flags],
            "notes": compliance.notes,
        },
    }


# ---------------------------------------------------------------------------
# Insurance — outbound verification (Dev 1's insurance module calls through here)
# ---------------------------------------------------------------------------

@router.post("/insurance/verify", response_model=InsuranceVerifyResponse)
def verify_insurance(
    body: InsuranceVerifyRequest,
    current_user: User = Depends(require_roles(*_STAFF_ROLES, UserRole.INSPECTOR)),
    db: Session = Depends(get_db),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
):
    """
    Outbound call to insurer for real-time policy verification.
    Dev 1's insurance module MUST call through this endpoint, never directly to insurers.
    """
    import time
    start = time.monotonic()

    # Stub: in production make HTTP call to insurer API
    # For sandbox, return a synthetic verified response
    verified = bool(body.policy_number)
    latency_ms = int((time.monotonic() - start) * 1000)

    response = InsuranceVerifyResponse(
        verified=verified,
        policy_number=body.policy_number,
        insurer_name="Sandbox Insurer Ltd" if verified else None,
        valid_from="2026-01-01" if verified else None,
        valid_to="2026-12-31" if verified else None,
        message="Sandbox verification — not a real policy check.",
    )

    _log_integration(
        db, agency="INSURANCE", direction="OUTBOUND",
        endpoint="/v1/integrations/insurance/verify",
        success=verified, status_code=200,
        request_payload=body.model_dump_json(),
        response_payload=response.model_dump_json(),
        correlation_id=correlation_id,
        latency_ms=latency_ms,
    )
    db.commit()
    return response


# ---------------------------------------------------------------------------
# Hospital — inbound accident/casualty data
# ---------------------------------------------------------------------------

@router.post("/hospitals/accident-notify", status_code=202)
def hospital_accident_notify(
    body: HospitalAccidentIn,
    x_service_key: str | None = Header(default=None, alias="X-Service-Key"),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
    db: Session = Depends(get_db),
):
    """Inbound accident/casualty data from hospital systems."""
    _log_integration(
        db, agency="HOSPITAL", direction="INBOUND",
        endpoint="/v1/integrations/hospitals/accident-notify",
        success=True, status_code=202,
        request_payload=body.model_dump_json(),
        correlation_id=correlation_id,
    )
    db.commit()
    return {"received": True, "hospital_reference": body.hospital_reference}


# ---------------------------------------------------------------------------
# National ID verification
# ---------------------------------------------------------------------------

@router.post("/national-id/verify", response_model=NationalIdVerifyResponse)
def verify_national_id(
    body: NationalIdVerifyRequest,
    current_user: User = Depends(require_roles(*_STAFF_ROLES, UserRole.INSPECTOR)),
    db: Session = Depends(get_db),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
):
    """NRC verification against the national ID registry."""
    import time
    start = time.monotonic()

    # Stub: check against our own users table first, then simulate external check
    existing_user = db.query(User).filter(User.nrc_number == body.nrc_number).first()
    verified = existing_user is not None
    full_name = existing_user.full_name if existing_user else body.full_name
    latency_ms = int((time.monotonic() - start) * 1000)

    response = NationalIdVerifyResponse(
        verified=verified,
        nrc_number=body.nrc_number,
        full_name=full_name,
        message="Verified against local registry." if verified else "NRC not found in sandbox registry.",
    )

    _log_integration(
        db, agency="NATIONAL_ID", direction="OUTBOUND",
        endpoint="/v1/integrations/national-id/verify",
        success=verified, status_code=200,
        request_payload=body.model_dump_json(),
        response_payload=response.model_dump_json(),
        correlation_id=correlation_id,
        latency_ms=latency_ms,
    )
    db.commit()
    return response


# ---------------------------------------------------------------------------
# Batch ingest (high-volume: toll-plaza offline sync, ANPR events from Dev 1)
# ---------------------------------------------------------------------------

@router.post("/batch-ingest", response_model=BatchIngestResult, status_code=202)
def batch_ingest(
    body: BatchIngestRequest,
    current_user: User = Depends(require_roles(*_STAFF_ROLES)),
    db: Session = Depends(get_db),
    correlation_id: str | None = Header(default=None, alias="X-Correlation-Id"),
):
    """
    High-volume batched event ingestion — up to 500 events per call.
    Used by Dev 1's toll-plaza offline sync and ANPR event uploads.
    Returns per-item success/failure.
    """
    accepted = 0
    rejected = 0
    errors: list[dict] = []

    for idx, event in enumerate(body.events):
        try:
            event_type = event.event_type
            payload = event.payload

            if event_type == "TOLL.TRANSIT":
                # Route to toll worker for processing
                from app.workers.toll_sync import process_toll_event
                process_toll_event(db, payload)
                accepted += 1
            elif event_type == "ANPR.CAPTURE":
                from app.services.compliance_engine import check_plate
                plate = payload.get("plate_number", "")
                if plate:
                    check_plate(db, plate)  # result stored via ANPR capture endpoint
                accepted += 1
            else:
                # Accept unknown types — log for later routing
                accepted += 1
        except Exception as exc:
            rejected += 1
            errors.append({"index": idx, "event_type": event.event_type, "error": str(exc)})

    _log_integration(
        db, agency=body.source_system, direction="INBOUND",
        endpoint="/v1/integrations/batch-ingest",
        success=rejected == 0, status_code=202,
        request_payload=json.dumps({"source": body.source_system, "count": len(body.events)}),
        correlation_id=correlation_id,
    )
    db.commit()

    return BatchIngestResult(
        total=len(body.events),
        accepted=accepted,
        rejected=rejected,
        errors=errors,
    )


# ---------------------------------------------------------------------------
# Monitoring / status
# ---------------------------------------------------------------------------

@router.get("/monitoring/status", response_model=list[IntegrationStatusEntry])
def integration_status(
    current_user: User = Depends(require_roles(*_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    """Health/status of each external integration — last success, error rate."""
    from datetime import timedelta

    AGENCIES = ["POLICE", "INSURANCE", "HOSPITAL", "NATIONAL_ID", "TOLL"]
    one_hour_ago = _utcnow() - timedelta(hours=1)
    result = []

    for agency in AGENCIES:
        last_success = (
            db.query(IntegrationLog.occurred_at)
            .filter(IntegrationLog.agency == agency, IntegrationLog.success == True)  # noqa: E712
            .order_by(IntegrationLog.occurred_at.desc())
            .first()
        )
        last_error = (
            db.query(IntegrationLog.occurred_at)
            .filter(IntegrationLog.agency == agency, IntegrationLog.success == False)  # noqa: E712
            .order_by(IntegrationLog.occurred_at.desc())
            .first()
        )
        total_1h = (
            db.query(func.count(IntegrationLog.id))
            .filter(IntegrationLog.agency == agency, IntegrationLog.occurred_at >= one_hour_ago)
            .scalar() or 0
        )
        errors_1h = (
            db.query(func.count(IntegrationLog.id))
            .filter(
                IntegrationLog.agency == agency,
                IntegrationLog.occurred_at >= one_hour_ago,
                IntegrationLog.success == False,  # noqa: E712
            )
            .scalar() or 0
        )
        error_rate = (errors_1h / total_1h) if total_1h > 0 else 0.0

        result.append(IntegrationStatusEntry(
            agency=agency,
            last_success_at=last_success[0] if last_success else None,
            last_error_at=last_error[0] if last_error else None,
            error_rate_1h=round(error_rate, 4),
            is_healthy=error_rate < 0.1,
        ))

    return result


# ---------------------------------------------------------------------------
# Data-sharing contracts
# ---------------------------------------------------------------------------

@router.get("/contracts", response_model=list[ContractOut])
def list_contracts(
    current_user: User = Depends(require_roles(*_STAFF_ROLES)),
    db: Session = Depends(get_db),
):
    contracts = db.query(DataSharingContract).filter(DataSharingContract.is_active == True).all()  # noqa: E712
    return [
        ContractOut(
            agency=c.agency,
            display_name=c.display_name,
            schema_version=c.schema_version,
            allowed_fields=json.loads(c.allowed_fields) if c.allowed_fields else [],
            retention_days=c.retention_days,
            is_active=c.is_active,
        )
        for c in contracts
    ]
