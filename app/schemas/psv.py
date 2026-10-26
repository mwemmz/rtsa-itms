import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.psv import PSVOperatorStatus, PSVPermitStatus


class PSVOperatorCreate(BaseModel):
    name: str
    licence_number: str
    contact_person: str
    phone: str
    email: str | None = None
    address: str | None = None


class PSVOperatorResponse(BaseModel):
    id: uuid.UUID
    name: str
    licence_number: str
    contact_person: str
    phone: str
    email: str | None
    address: str | None
    status: PSVOperatorStatus
    registered_date: datetime

    model_config = {"from_attributes": True}


class PSVPermitCreate(BaseModel):
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime


class PSVPermitResponse(BaseModel):
    id: uuid.UUID
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime
    status: PSVPermitStatus

    model_config = {"from_attributes": True}

class Waypoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PSVCorridorSet(BaseModel):
    waypoints: list[Waypoint] = Field(min_length=2, max_length=200)
    buffer_m: int = Field(default=500, ge=50, le=20000)


class PSVCorridorResponse(BaseModel):
    permit_id: uuid.UUID
    waypoints: list[Waypoint]
    buffer_m: int


class PSVPositionReport(BaseModel):
    plate_number: str = Field(min_length=1, max_length=20)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timestamp: datetime | None = None


class PSVPositionResult(BaseModel):
    plate_number: str
    permit_number: str
    inside: bool
    distance_m: int
    buffer_m: int
    alert_id: uuid.UUID | None = None
    alert_deduplicated: bool = False


class PSVRouteAlertResponse(BaseModel):
    id: uuid.UUID
    permit_id: uuid.UUID
    vehicle_id: uuid.UUID
    latitude: float
    longitude: float
    distance_m: int
    buffer_m: int
    occurred_at: datetime
    acknowledged: bool

    model_config = {"from_attributes": True}
