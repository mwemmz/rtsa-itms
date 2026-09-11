import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class AccidentSeverity(str, enum.Enum):
    MINOR = "minor"
    SERIOUS = "serious"
    FATAL = "fatal"


class AccidentStatus(str, enum.Enum):
    REPORTED = "reported"
    UNDER_INVESTIGATION = "under_investigation"
    INVESTIGATED = "investigated"
    CLOSED = "closed"


class Accident(Base):
    __tablename__ = "accidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(nullable=False)
    severity: Mapped[AccidentSeverity] = mapped_column(
        Enum(AccidentSeverity), nullable=False
    )
    status: Mapped[AccidentStatus] = mapped_column(
        Enum(AccidentStatus), default=AccidentStatus.REPORTED, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class AccidentVehicle(Base):
    __tablename__ = "accident_vehicles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    accident_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("accidents.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("drivers.id"), nullable=True
    )
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False)
    role: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )