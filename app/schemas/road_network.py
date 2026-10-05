import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

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
    description: str | None = Field(default=None, max_length=1000)
    # Staff only: citizen reports are pushed to motorists once an officer confirms them.
    broadcast_alert: bool = True
    # Citizens must declare the report true; a false one is fined to their NRC.
    declaration: bool = False


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
    verification: str = "official"
    reviewed_at: datetime | None = None

    model_config = {"from_attributes": True}


class MyReport(IncidentResponse):
    """A citizen's own report, with the officer's reason if it was turned down."""

    road_name: str | None = None
    stretch: str | None = None
    review_note: str | None = None


ReviewReason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=500)]


class IncidentDismiss(BaseModel):
    # True: the officer found the report false and the reporter is fined.
    # False: nothing there any more, a duplicate, can't tell - no blame.
    false_report: bool = False
    reason: ReviewReason


class ReporterStanding(BaseModel):
    can_report: bool
    reason: str | None
    nrc_on_file: bool
    open_reports: int
    max_open_reports: int
    false_reports: int
    strike_limit: int
    window_days: int
    fine_amount: int
    suspended_until: datetime | None = None


class RoadStatusBoardItem(BaseModel):
    road_name: str
    status: RoadStatus
    class_: RoadClass | None = None
    active_incidents: int = 0


class RoadStatusBoard(BaseModel):
    roads: list[RoadStatusBoardItem]
    blocking_incidents: list[IncidentResponse]