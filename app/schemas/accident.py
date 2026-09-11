import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.accident import AccidentSeverity, AccidentStatus


class AccidentVehicleCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    plate_number: str
    role: str | None = None


class AccidentCreate(BaseModel):
    location: str
    occurred_at: datetime
    severity: AccidentSeverity
    description: str | None = None
    vehicles: list[AccidentVehicleCreate] = []


class AccidentVehicleResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    plate_number: str
    role: str | None

    model_config = {"from_attributes": True}


class AccidentResponse(BaseModel):
    id: uuid.UUID
    location: str
    occurred_at: datetime
    severity: AccidentSeverity
    status: AccidentStatus
    description: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class AccidentStats(BaseModel):
    total: int
    by_severity: dict
    by_status: dict