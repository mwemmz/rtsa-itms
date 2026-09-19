"""Vehicle Insurance API."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.audit import log_event
from app.core.db import get_db
from app.core.security import get_current_user, require_roles
from app.models.admin import ActorType
from app.models.citizen import User, UserRole
from app.models.insurance import InsurancePolicy, InsuranceStatus, InsuranceType
from app.models.vehicles import Vehicle
from app.schemas.common import PagedResponse, PaginationMeta

router = APIRouter(prefix="/v1/insurance", tags=["Insurance"])

_STAFF = (UserRole.ADMIN, UserRole.OFFICER, UserRole.INSPECTOR)


class InsuranceCreate(BaseModel):
    vehicle_id: str
    policy_number: str
    insurer_name: str
    insurance_type: InsuranceType = InsuranceType.THIRD_PARTY
    valid_from: datetime
    valid_to: datetime
    premium_ngwee: int | None = None
    cover_amount_ngwee: int | None = None
    notes: str | None = None


class InsuranceUpdate(BaseModel):
    insurer_name: str | None = None
    status: InsuranceStatus | None = None
    valid_to: datetime | None = None
    notes: str | None = None


class InsuranceOut(BaseModel):
    id: str
    vehicle_id: str
    policy_number: str
    insurer_name: str
    insurance_type: InsuranceType
    status: InsuranceStatus
    valid_from: datetime
    valid_to: datetime
    premium_ngwee: int | None
    created_at: datetime

    model_config = {"from_attributes": True}


def _get_or_404(db: Session, policy_id: str) -> InsurancePolicy:
    p = db.get(InsurancePolicy, policy_id)
    if not p:
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Insurance policy not found."})
    return p


def _utcnow():
    return datetime.now(timezone.utc)


@router.get("/health")
def health():
    return {"module": "insurance", "status": "ok"}


@router.post("", response_model=InsuranceOut, status_code=201)
def create_policy(
    body: InsuranceCreate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    if not db.get(Vehicle, body.vehicle_id):
        raise HTTPException(404, detail={"code": "NOT_FOUND", "message": "Vehicle not found."})
    if db.query(InsurancePolicy).filter(InsurancePolicy.policy_number == body.policy_number).first():
        raise HTTPException(409, detail={"code": "VALIDATION_ERROR", "message": "Policy number already exists."})
    policy = InsurancePolicy(**body.model_dump())
    db.add(policy)
    db.flush()
    log_event(db, action="INSURANCE.POLICY_CREATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="INSURANCE", resource_id=policy.id)
    db.commit()
    db.refresh(policy)
    return policy


@router.get("", response_model=PagedResponse[InsuranceOut])
def list_policies(
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, le=200, ge=1),
    cursor: str | None = None,
    vehicle_id: str | None = None,
    status: InsuranceStatus | None = None,
):
    q = db.query(InsurancePolicy)
    if vehicle_id:
        q = q.filter(InsurancePolicy.vehicle_id == vehicle_id)
    if status:
        q = q.filter(InsurancePolicy.status == status)
    if cursor:
        q = q.filter(InsurancePolicy.id > cursor)
    q = q.order_by(InsurancePolicy.created_at.desc()).limit(limit + 1)
    results = q.all()
    has_more = len(results) > limit
    items = results[:limit]
    return PagedResponse(
        data=items,
        pagination=PaginationMeta(next_cursor=items[-1].id if has_more else None, has_more=has_more, limit=limit),
    )


@router.get("/vehicle/{vehicle_id}/active", response_model=InsuranceOut | None)
def get_active_for_vehicle(
    vehicle_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns the current active policy for a vehicle, or null if uninsured."""
    now = _utcnow()
    policy = (
        db.query(InsurancePolicy)
        .filter(
            InsurancePolicy.vehicle_id == vehicle_id,
            InsurancePolicy.status == InsuranceStatus.ACTIVE,
        )
        .order_by(InsurancePolicy.valid_to.desc())
        .first()
    )
    return policy


@router.get("/{policy_id}", response_model=InsuranceOut)
def get_policy(policy_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _get_or_404(db, policy_id)


@router.put("/{policy_id}", response_model=InsuranceOut)
def update_policy(
    policy_id: str,
    body: InsuranceUpdate,
    current_user: User = Depends(require_roles(*_STAFF)),
    db: Session = Depends(get_db),
):
    p = _get_or_404(db, policy_id)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(p, field, val)
    log_event(db, action="INSURANCE.POLICY_UPDATED", actor_id=current_user.id,
              actor_type=ActorType.OFFICER, resource_type="INSURANCE", resource_id=policy_id)
    db.commit()
    db.refresh(p)
    return p
