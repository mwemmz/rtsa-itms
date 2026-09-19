"""Inter-agency integration API.

Two audiences:
* **Agencies** call the ``/api/integration/{agency}/...`` endpoints with an
  ``X-API-Key``. What they can reach is limited by their data-sharing contract
  (scopes).
* **RTSA administrators** (``integrations:manage``) register agencies, rotate
  keys and watch the monitoring feed.
"""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_permission
from app.core.security import get_current_user
from app.core.timeutil import utcnow
from app.models.accident import Accident, AccidentSeverity, AccidentVehicle
from app.models.driver import Driver
from app.models.inspection import FitnessCertificate
from app.models.insurance import Insurance
from app.models.platform import AgencyClient, IntegrationLog
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.services import integration as svc
from app.services.audit import log_action
from app.services.compliance import check_vehicle_compliance

router = APIRouter(prefix="/api/integration", tags=["Inter-Agency Integration"])


# --- schemas -----------------------------------------------------------------------

class AgencyCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    agency_type: str
    scopes: list[str] | None = None  # default: every scope the type is allowed
    contact_email: str | None = None
    data_sharing_agreement: str | None = None
    contract_expires_at: datetime | None = None
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10000)


class AgencyUpdate(BaseModel):
    scopes: list[str] | None = None
    contact_email: str | None = None
    data_sharing_agreement: str | None = None
    contract_expires_at: datetime | None = None
    rate_limit_per_minute: int | None = Field(default=None, ge=1, le=10000)
    is_active: bool | None = None


class PolicyUpsert(BaseModel):
    plate_number: str
    provider: str = Field(min_length=2, max_length=200)
    policy_number: str = Field(min_length=3, max_length=100)
    start_date: datetime
    end_date: datetime
    is_active: bool = True


class AccidentReport(BaseModel):
    location: str = Field(min_length=2, max_length=200)
    occurred_at: datetime
    severity: AccidentSeverity
    description: str | None = Field(default=None, max_length=2000)
    plate_numbers: list[str] = Field(default_factory=list, max_length=20)
    casualties: int | None = Field(default=None, ge=0)


def _agency_out(a: AgencyClient) -> dict:
    return {
        "id": str(a.id), "name": a.name, "agency_type": a.agency_type,
        "api_key_prefix": f"rtsa_{a.api_key_prefix}_...", "scopes": a.scopes.split(",") if a.scopes else [],
        "contact_email": a.contact_email, "data_sharing_agreement": a.data_sharing_agreement,
        "contract_expires_at": a.contract_expires_at.isoformat() if a.contract_expires_at else None,
        "rate_limit_per_minute": a.rate_limit_per_minute, "is_active": a.is_active,
        "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _vehicle_by_plate(db: Session, plate: str) -> Vehicle:
    vehicle = db.query(Vehicle).filter(Vehicle.registration_number == plate.strip().upper()).first()
    if vehicle is None:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle


# --- administration ------------------------------------------------------------------------

@router.get("/contracts")
def data_sharing_contracts(current_user: User = Depends(require_permission("integrations:manage"))):
    """What each agency type may be granted (the ceiling for any contract)."""
    return {t: sorted(s) for t, s in svc.SCOPES_BY_TYPE.items()}


@router.post("/agencies", status_code=status.HTTP_201_CREATED)
def register_agency(
    payload: AgencyCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    """Register an agency. The API key is returned **once** — only its hash is stored."""
    scopes = svc.validate_scopes(payload.agency_type, payload.scopes or sorted(svc.SCOPES_BY_TYPE.get(payload.agency_type, [])))
    if db.query(AgencyClient).filter(AgencyClient.name == payload.name).first():
        raise HTTPException(status_code=400, detail="An agency with this name already exists")
    key, prefix, digest = svc.generate_key()
    agency = AgencyClient(
        name=payload.name, agency_type=payload.agency_type, api_key_prefix=prefix, api_key_hash=digest,
        scopes=",".join(scopes), contact_email=payload.contact_email,
        data_sharing_agreement=payload.data_sharing_agreement, contract_expires_at=payload.contract_expires_at,
        rate_limit_per_minute=payload.rate_limit_per_minute,
    )
    db.add(agency)
    db.flush()
    log_action(db, "register_agency", "agency", str(agency.id), f"{payload.name} ({payload.agency_type})", current_user.id)
    db.commit()
    db.refresh(agency)
    return {**_agency_out(agency), "api_key": key,
            "warning": "Store this key now; it cannot be shown again."}


@router.get("/agencies")
def list_agencies(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    return [_agency_out(a) for a in db.query(AgencyClient).order_by(AgencyClient.name).all()]


@router.patch("/agencies/{agency_id}")
def update_agency(
    agency_id: str,
    payload: AgencyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    agency = db.query(AgencyClient).filter(AgencyClient.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    changes = payload.model_dump(exclude_unset=True)
    if "scopes" in changes and changes["scopes"] is not None:
        agency.scopes = ",".join(svc.validate_scopes(agency.agency_type, changes.pop("scopes")))
    else:
        changes.pop("scopes", None)
    for field, value in changes.items():
        setattr(agency, field, value)
    log_action(db, "update_agency", "agency", str(agency.id), ", ".join(sorted(payload.model_dump(exclude_unset=True))),
               current_user.id)
    db.commit()
    db.refresh(agency)
    return _agency_out(agency)


@router.post("/agencies/{agency_id}/rotate-key")
def rotate_key(
    agency_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    agency = db.query(AgencyClient).filter(AgencyClient.id == agency_id).first()
    if not agency:
        raise HTTPException(status_code=404, detail="Agency not found")
    key, prefix, digest = svc.generate_key()
    agency.api_key_prefix, agency.api_key_hash = prefix, digest
    log_action(db, "rotate_agency_key", "agency", str(agency.id), None, current_user.id)
    db.commit()
    return {"api_key": key, "warning": "The previous key no longer works. Store this one now."}


@router.get("/monitoring")
def monitoring(
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    since = utcnow() - timedelta(hours=hours)
    recent_errors = (
        db.query(IntegrationLog)
        .filter(IntegrationLog.created_at >= since, IntegrationLog.success == False)  # noqa: E712
        .order_by(IntegrationLog.created_at.desc()).limit(25).all()
    )
    return {
        "window_hours": hours,
        "agencies": svc.monitoring_summary(db, hours),
        "recent_errors": [
            {"agency": e.agency_name, "endpoint": e.endpoint, "status": e.status_code, "detail": e.detail,
             "at": e.created_at.isoformat() if e.created_at else None} for e in recent_errors
        ],
    }


@router.get("/logs")
def integration_logs(
    agency: str | None = None,
    success: bool | None = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_permission("integrations:manage")),
):
    query = db.query(IntegrationLog)
    if agency:
        query = query.filter(IntegrationLog.agency_name == agency)
    if success is not None:
        query = query.filter(IntegrationLog.success == success)
    rows = query.order_by(IntegrationLog.created_at.desc()).offset(skip).limit(limit).all()
    return [{"agency": r.agency_name, "direction": r.direction, "endpoint": r.endpoint, "status": r.status_code,
             "success": r.success, "latency_ms": r.latency_ms, "detail": r.detail,
             "at": r.created_at.isoformat() if r.created_at else None} for r in rows]


# --- national ID (outbound, used by RTSA staff) -----------------------------------------------

@router.get("/national-id/verify")
def verify_national_id(
    nrc: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Verify an NRC number against the national registry (staff only)."""
    if current_user.role == UserRole.CITIZEN:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return svc.verify_national_id(db, nrc)


# --- police / toll authority: read access ------------------------------------------------------

@router.get("/police/vehicles/{plate}")
def police_vehicle_lookup(
    plate: str,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("vehicles:read")),
):
    """Registration status, blacklist flag and validity of insurance/fitness for a plate."""
    vehicle = _vehicle_by_plate(db, plate)
    now = datetime.utcnow()
    ins = db.query(Insurance).filter(Insurance.vehicle_id == vehicle.id, Insurance.is_active == True,  # noqa: E712
                                     Insurance.end_date >= now).first()
    fit = db.query(FitnessCertificate).filter(FitnessCertificate.vehicle_id == vehicle.id,
                                              FitnessCertificate.expiry_date >= now).first()
    return {
        "plate_number": vehicle.registration_number, "make": vehicle.make, "model": vehicle.model,
        "year": vehicle.year, "status": vehicle.status.value, "is_blacklisted": vehicle.is_blacklisted,
        "blacklist_reason": vehicle.blacklist_reason,
        "insurance_valid": ins is not None, "insurance_expires": ins.end_date.isoformat() if ins else None,
        "fitness_valid": fit is not None, "fitness_expires": fit.expiry_date.isoformat() if fit else None,
    }


@router.get("/police/drivers/{licence_number}")
def police_driver_lookup(
    licence_number: str,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("drivers:read")),
):
    driver = db.query(Driver).filter(Driver.licence_number == licence_number.strip()).first()
    if driver is None:
        raise HTTPException(status_code=404, detail="Driver not found")
    return {
        "licence_number": driver.licence_number, "name": f"{driver.first_name} {driver.last_name}",
        "licence_class": driver.licence_class.value, "status": driver.status.value,
        "expires": driver.licence_expiry_date.isoformat(), "restrictions": driver.restrictions,
        "valid": driver.status.value == "active" and driver.licence_expiry_date >= datetime.utcnow(),
    }


@router.get("/toll-authority/compliance/{plate}")
def toll_authority_compliance(
    plate: str,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("compliance:read")),
):
    """Run the ITMS compliance decision for a plate on behalf of an external toll operator."""
    vehicle = _vehicle_by_plate(db, plate)
    result = check_vehicle_compliance(db, vehicle)
    return {"plate_number": vehicle.registration_number, "compliant": result.compliant,
            "decision": "allow" if result.compliant else "flag", "issues": result.issues, "checks": result.checks}


# --- insurance: push policies ------------------------------------------------------------------------

@router.get("/insurance/verify/{plate}")
def insurance_verify(
    plate: str,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("insurance:read")),
):
    vehicle = _vehicle_by_plate(db, plate)
    policies = db.query(Insurance).filter(Insurance.vehicle_id == vehicle.id).order_by(Insurance.end_date.desc()).all()
    return {"plate_number": vehicle.registration_number,
            "policies": [{"provider": p.provider, "policy_number": p.policy_number, "start": p.start_date.isoformat(),
                          "end": p.end_date.isoformat(), "active": p.is_active} for p in policies]}


@router.put("/insurance/policies")
def insurance_upsert_policy(
    payload: PolicyUpsert,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("insurance:write")),
):
    """Insurers push new, renewed or cancelled policies straight into the vehicle record."""
    if payload.end_date <= payload.start_date:
        raise HTTPException(status_code=422, detail="end_date must be after start_date")
    vehicle = _vehicle_by_plate(db, payload.plate_number)
    policy = db.query(Insurance).filter(Insurance.policy_number == payload.policy_number).first()
    created = policy is None
    if policy is not None and policy.vehicle_id != vehicle.id:
        raise HTTPException(status_code=409, detail="Policy number is already registered to another vehicle")
    if created:
        policy = Insurance(vehicle_id=vehicle.id, policy_number=payload.policy_number)
        db.add(policy)
    policy.provider = payload.provider
    policy.start_date = payload.start_date.replace(tzinfo=None)
    policy.end_date = payload.end_date.replace(tzinfo=None)
    policy.is_active = payload.is_active
    db.flush()
    log_action(db, "agency_upsert_policy", "insurance", str(policy.id),
               f"{agency.name}: {payload.policy_number} for {vehicle.registration_number}", None)
    db.commit()
    return {"policy_number": policy.policy_number, "plate_number": vehicle.registration_number,
            "created": created, "active": policy.is_active}


# --- police / hospitals: report accidents ------------------------------------------------------------------

def _accident_from_agency(db: Session, agency: AgencyClient, payload: AccidentReport) -> dict:
    description = f"[Reported by {agency.name}] " + (payload.description or "")
    if payload.casualties is not None:
        description += f" Casualties: {payload.casualties}."
    accident = Accident(location=payload.location, occurred_at=payload.occurred_at.replace(tzinfo=None),
                        severity=payload.severity, description=description.strip())
    db.add(accident)
    db.flush()
    linked, unknown = [], []
    for plate in payload.plate_numbers:
        plate = plate.strip().upper()
        vehicle = db.query(Vehicle).filter(Vehicle.registration_number == plate).first()
        db.add(AccidentVehicle(accident_id=accident.id, vehicle_id=vehicle.id if vehicle else None,
                               plate_number=plate, role="involved"))
        (linked if vehicle else unknown).append(plate)
    log_action(db, "agency_report_accident", "accident", str(accident.id), agency.name, None)
    db.commit()
    return {"accident_id": str(accident.id), "linked_vehicles": linked, "unrecognised_plates": unknown}


@router.post("/police/accidents", status_code=status.HTTP_201_CREATED)
def police_report_accident(
    payload: AccidentReport,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("accidents:write")),
):
    return _accident_from_agency(db, agency, payload)


@router.post("/hospital/accidents", status_code=status.HTTP_201_CREATED)
def hospital_report_accident(
    payload: AccidentReport,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("accidents:write")),
):
    return _accident_from_agency(db, agency, payload)


# --- national ID: agency-facing verification ----------------------------------------------------------------------

@router.get("/national-id/lookup")
def national_id_lookup(
    nrc: str,
    db: Session = Depends(get_db),
    agency: AgencyClient = Depends(svc.agency_auth("identity:read")),
):
    """Which ITMS driver/vehicle records exist for an NRC (for the national ID system to cross-check)."""
    svc_result = svc.verify_national_id(db, nrc)
    drivers = db.query(Driver).filter(Driver.id_number == nrc).count()
    vehicles = db.query(Vehicle).filter(Vehicle.owner_id_number == nrc).count()
    return {**svc_result, "itms_driver_records": drivers, "itms_vehicle_records": vehicles}
