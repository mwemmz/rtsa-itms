import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class RoadClass(str, enum.Enum):
    HIGHWAY = "highway"
    MAIN_ROAD = "main_road"
    SECONDARY = "secondary"
    ACCESS = "access"


class RoadStatus(str, enum.Enum):
    OPEN = "open"
    CONGESTED = "congested"
    UNDER_MAINTENANCE = "under_maintenance"
    CLOSED = "closed"


class Road(Base):
    """A named road in the network (e.g. 'Great East Road')."""

    __tablename__ = "roads"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    road_class: Mapped[RoadClass] = mapped_column(
        Enum(RoadClass), default=RoadClass.MAIN_ROAD, nullable=False
    )
    status: Mapped[RoadStatus] = mapped_column(
        Enum(RoadStatus), default=RoadStatus.OPEN, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Intersection(Base):
    """A junction / connectivity point in the road graph."""

    __tablename__ = "intersections"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)


class RoadSegment(Base):
    """A directed edge connecting two intersections along a road."""

    __tablename__ = "road_segments"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    road_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("roads.id"), nullable=False, index=True
    )
    start_intersection_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("intersections.id"), nullable=False, index=True
    )
    end_intersection_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("intersections.id"), nullable=False, index=True
    )
    distance_km: Mapped[float] = mapped_column(Float, nullable=False)
    travel_minutes: Mapped[float] = mapped_column(Float, nullable=False)


class IncidentType(str, enum.Enum):
    ACCIDENT = "accident"
    ROAD_CLOSED = "road_closed"
    MAINTENANCE = "maintenance"
    CONGESTION = "congestion"
    ROADWORKS = "roadworks"


class IncidentSeverity(str, enum.Enum):
    MINOR = "minor"
    SERIOUS = "serious"
    FATAL = "fatal"


class RoadIncident(Base):
    """A live event affecting a road segment (accident, closure, works...)."""

    __tablename__ = "road_incidents"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    incident_type: Mapped[IncidentType] = mapped_column(
        Enum(IncidentType), nullable=False, index=True
    )
    severity: Mapped[IncidentSeverity] = mapped_column(
        Enum(IncidentSeverity), default=IncidentSeverity.MINOR, nullable=False
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("road_segments.id"), nullable=True, index=True
    )
    road_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("roads.id"), nullable=True, index=True
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False, index=True)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RouteCache(Base):
    """Optional way to avoid recomputing popular routes under load."""

    __tablename__ = "route_cache"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    origin_intersection_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("intersections.id"), nullable=False, index=True
    )
    destination_intersection_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("intersections.id"), nullable=True, index=True
    )
    raw_route: Mapped[str] = mapped_column(Text, nullable=False)  # JSON of route steps
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )