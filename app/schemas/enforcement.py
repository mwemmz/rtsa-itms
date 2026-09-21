import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.enforcement import ChallanStatus, ViolationType


class ViolationCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    violation_type: ViolationType
    location: str
    timestamp: datetime | None = None
    description: str | None = None


class ViolationResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    violation_type: ViolationType
    location: str
    timestamp: datetime
    description: str | None
    registration_number: str | None = None
    owner_name: str | None = None
    owner_id_number: str | None = None
    driver_name: str | None = None

    model_config = {"from_attributes": True}


class ChallanResponse(BaseModel):
    id: uuid.UUID
    reference: str
    violation_id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    penalty_amount: int
    due_date: datetime
    status: ChallanStatus
    created_at: datetime
    registration_number: str | None = None
    owner_name: str | None = None
    driver_name: str | None = None

    model_config = {"from_attributes": True}


class ChallanPaymentResult(BaseModel):
    challan_id: uuid.UUID
    reference: str
    status: ChallanStatus
    message: str
