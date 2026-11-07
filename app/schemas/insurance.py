import uuid
from datetime import datetime

from pydantic import BaseModel, model_validator


class InsuranceCreate(BaseModel):
    vehicle_id: uuid.UUID
    provider: str
    policy_number: str
    start_date: datetime
    end_date: datetime

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end_date <= self.start_date:
            raise ValueError("Policy end date must be after its start date")
        return self


class InsuranceResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    provider: str
    policy_number: str
    start_date: datetime
    end_date: datetime
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
