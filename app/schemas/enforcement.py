import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enforcement import ChallanStatus, ViolationType


class ViolationCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    violation_type: ViolationType
    # A challan may be contested, so the location has to be on the record. A bare
    # `str` in pydantic v2 accepts "", which let a challan be raised with no place.
    location: str = Field(min_length=1, max_length=255)
    timestamp: datetime | None = None
    description: str | None = Field(default=None, max_length=1000)


class ViolationResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    violation_type: ViolationType
    category: str = "both"
    liable_party: str = "driver"
    carries_licence_consequence: bool = False
    grounds_impoundment: bool = False
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
    violation_type: ViolationType | None = None
    category: str = "both"
    liable_party: str = "both"
    carries_licence_consequence: bool = False
    grounds_impoundment: bool = False
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
