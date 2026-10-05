import uuid
from datetime import datetime

from pydantic import BaseModel, Field, model_validator

from app.models.enforcement import ChallanStatus, ViolationType
from app.schemas.fields import LocationStr


class ViolationCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    violation_type: ViolationType
    # A challan may be contested, so the location has to be on the record. A bare
    # `str` in pydantic v2 accepts "", which let a challan be raised with no place.
    location: LocationStr
    # Road speed when the offence was observed, km/h. Optional on the wire
    # because only some offences have a meaningful reading, but bounded so a
    # typo or a unit mix-up (metres, or a pasted odometer figure) cannot land
    # an absurd value on the record.
    speed_kmh: float | None = Field(default=None, ge=0, le=500)
    timestamp: datetime | None = None
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_an_offender(self):
        # Neither field set means no one to charge and no one to notify --
        # the resulting challan would be uncollectable.
        if self.vehicle_id is None and self.driver_id is None:
            raise ValueError("A violation needs a vehicle_id, a driver_id, or both")
        return self


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
    speed_kmh: float | None = None
    timestamp: datetime
    description: str | None
    registration_number: str | None = None
    owner_name: str | None = None
    owner_id_number: str | None = None
    driver_name: str | None = None
    # Set when the violation is charged to an account rather than a vehicle or driver.
    account_name: str | None = None

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
    account_name: str | None = None

    model_config = {"from_attributes": True}


class ChallanPaymentResult(BaseModel):
    challan_id: uuid.UUID
    reference: str
    status: ChallanStatus
    message: str
