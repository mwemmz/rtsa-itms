import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.vehicle import VehicleStatus


class VehicleCreate(BaseModel):
    registration_number: str
    owner_name: str
    owner_id_number: str
    make: str
    model: str
    year: int
    color: str | None = None
    engine_number: str | None = None
    chassis_number: str | None = None


class VehicleUpdate(BaseModel):
    owner_name: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None
    status: VehicleStatus | None = None
    is_blacklisted: bool | None = None
    blacklist_reason: str | None = None


class VehicleResponse(BaseModel):
    id: uuid.UUID
    registration_number: str
    owner_name: str
    owner_id_number: str
    make: str
    model: str
    year: int
    color: str | None
    engine_number: str | None
    chassis_number: str | None
    status: VehicleStatus
    is_blacklisted: bool
    blacklist_reason: str | None
    registration_date: datetime
    last_updated: datetime

    model_config = {"from_attributes": True}
