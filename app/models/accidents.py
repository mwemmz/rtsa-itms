"""Accident report models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class AccidentSeverity(str, enum.Enum):
    MINOR = "MINOR"
    SERIOUS = "SERIOUS"
    FATAL = "FATAL"


class AccidentStatus(str, enum.Enum):
    REPORTED = "REPORTED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    CLOSED = "CLOSED"


class Accident(Base):
    __tablename__ = "accidents"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    report_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True,
        default=lambda: f"ACC-{uuid.uuid4().hex[:8].upper()}"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    severity: Mapped[AccidentSeverity] = mapped_column(
        Enum(AccidentSeverity), nullable=False, default=AccidentSeverity.MINOR
    )
    status: Mapped[AccidentStatus] = mapped_column(
        Enum(AccidentStatus), nullable=False, default=AccidentStatus.REPORTED, index=True
    )

    # Primary driver involved
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # NRC for citizen linkage
    driver_nrc: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)

    # Vehicles involved (primary)
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plate_number: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Casualties
    fatalities: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    injuries: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Reporting
    reported_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    police_report_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    hospital_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    driver: Mapped["Driver | None"] = relationship("Driver", back_populates="accidents")
