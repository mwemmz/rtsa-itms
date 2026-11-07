import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator


class InsuranceCreate(BaseModel):
    vehicle_id: uuid.UUID
    provider: str
    policy_number: str
    policy_type: str = "third_party"
    premium: int | None = None
    start_date: datetime
    end_date: datetime

    @model_validator(mode="after")
    def _end_after_start(self):
        from app.services.insurance_types import POLICY_TYPES

        if self.policy_type not in POLICY_TYPES:
            raise ValueError(f"Policy type must be one of: {', '.join(POLICY_TYPES)}")
        if self.premium is not None and self.premium < 0:
            raise ValueError("Premium cannot be negative")
        if self.end_date <= self.start_date:
            raise ValueError("Policy end date must be after its start date")
        return self


class InsuranceResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    provider: str
    policy_number: str
    policy_type: str = "third_party"
    premium: int | None = None
    start_date: datetime
    end_date: datetime
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
