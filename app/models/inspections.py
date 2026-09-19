"""Vehicle inspection models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InspectionStatus(str, enum.Enum):
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    PASSED = "PASSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class InspectionType(str, enum.Enum):
    ROUTINE = "ROUTINE"
    ROADWORTHY = "ROADWORTHY"
    POST_ACCIDENT = "POST_ACCIDENT"
    SPOT_CHECK = "SPOT_CHECK"


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    inspection_type: Mapped[InspectionType] = mapped_column(
        Enum(InspectionType), nullable=False, default=InspectionType.ROUTINE
    )
    status: Mapped[InspectionStatus] = mapped_column(
        Enum(InspectionStatus), nullable=False, default=InspectionStatus.SCHEDULED, index=True
    )
    inspector_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    inspection_centre: Mapped[str | None] = mapped_column(String(255), nullable=True)

    scheduled_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Results
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    defects_found: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    certificate_number: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="inspections")
