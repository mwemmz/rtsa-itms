import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class InspectionResult(str, enum.Enum):
    PASSED = "passed"
    FAILED = "failed"
    PENDING = "pending"


class Inspection(Base):
    __tablename__ = "inspections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=False, index=True
    )
    inspection_centre: Mapped[str] = mapped_column(String(200), nullable=False)
    scheduled_date: Mapped[datetime] = mapped_column(nullable=False)
    result: Mapped[InspectionResult] = mapped_column(
        Enum(InspectionResult), default=InspectionResult.PENDING, nullable=False
    )
    findings: Mapped[str | None] = mapped_column(Text, nullable=True)
    inspected_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FitnessCertificate(Base):
    __tablename__ = "fitness_certificates"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    inspection_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("inspections.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=False, index=True
    )
    certificate_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    issued_date: Mapped[datetime] = mapped_column(nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )