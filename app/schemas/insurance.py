import uuid
from datetime import datetime

from pydantic import BaseModel


class InsuranceCreate(BaseModel):
    vehicle_id: uuid.UUID
    provider: str
    policy_number: str
    start_date: datetime
    end_date: datetime


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
