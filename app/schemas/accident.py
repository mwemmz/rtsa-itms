import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, model_validator

from app.models.accident import AccidentSeverity, AccidentStatus
from app.schemas.fields import LocationStr, UtcDatetime

# Device clocks drift; a report a few minutes "ahead" is still a real report.
FUTURE_TOLERANCE = timedelta(minutes=10)


class AccidentVehicleCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    plate_number: str
    role: str | None = None


class AccidentCreate(BaseModel):
    # The stretch of mapped road it happened on. Picking from the network (rather
    # than typing a place name) is what lets the alert feed name the road, the
    # status board close it and the route planner steer around it.
    segment_id: uuid.UUID | None = None
    road_id: uuid.UUID | None = None
    # Optional landmark or exact spot, e.g. "near Manda Hill".
    location: LocationStr | None = None
    occurred_at: UtcDatetime
    severity: AccidentSeverity
    description: str | None = None
    vehicles: list[AccidentVehicleCreate] = []

    @model_validator(mode="after")
    def _require_a_mapped_road(self):
        if self.segment_id is None and self.road_id is None:
            raise ValueError("Choose the road (and stretch) where the accident happened")
        if self.occurred_at > datetime.utcnow() + FUTURE_TOLERANCE:
            raise ValueError("The accident time can't be in the future")
        return self


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
    road_id: uuid.UUID | None = None
    segment_id: uuid.UUID | None = None
    incident_id: uuid.UUID | None = None
    # Whether the accident is still on the live road feed (blocking its stretch).
    on_road: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class AccidentStats(BaseModel):
    total: int
    by_severity: dict
    by_status: dict
