import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.road_network import IncidentSeverity, IncidentType, RoadClass, RoadStatus


class RoadCreate(BaseModel):
    name: str
    road_class: RoadClass = RoadClass.MAIN_ROAD
    status: RoadStatus = RoadStatus.OPEN
    description: str | None = None


class RoadResponse(BaseModel):
    id: uuid.UUID
    name: str
    road_class: RoadClass
    status: RoadStatus
    description: str | None

    model_config = {"from_attributes": True}


class IntersectionCreate(BaseModel):
    name: str
    latitude: float
    longitude: float


class IntersectionResponse(BaseModel):
    id: uuid.UUID
    name: str
    latitude: float
    longitude: float

    model_config = {"from_attributes": True}


class SegmentCreate(BaseModel):
    road_id: uuid.UUID
    start_intersection_id: uuid.UUID
    end_intersection_id: uuid.UUID
    distance_km: float
    travel_minutes: float


class SegmentResponse(BaseModel):
    id: uuid.UUID
    road_id: uuid.UUID
    start_intersection_id: uuid.UUID
    end_intersection_id: uuid.UUID
    distance_km: float
    travel_minutes: float

    model_config = {"from_attributes": True}


class IncidentCreate(BaseModel):
    incident_type: IncidentType
    severity: IncidentSeverity = IncidentSeverity.MINOR
    segment_id: uuid.UUID | None = None
    road_id: uuid.UUID | None = None
    description: str | None = None
    broadcast_alert: bool = True


class IncidentResponse(BaseModel):
    id: uuid.UUID
    incident_type: IncidentType
    severity: IncidentSeverity
    segment_id: uuid.UUID | None
    road_id: uuid.UUID | None
    description: str | None
    starts_at: datetime
    ends_at: datetime | None
    is_active: bool

    model_config = {"from_attributes": True}


class RoadStatusBoardItem(BaseModel):
    road_name: str
    status: RoadStatus
    class_: RoadClass | None = None
    active_incidents: int = 0


class RoadStatusBoard(BaseModel):
    roads: list[RoadStatusBoardItem]
    blocking_incidents: list[IncidentResponse]