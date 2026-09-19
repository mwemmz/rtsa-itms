"""Traffic violations / e-Challan models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ViolationType(str, enum.Enum):
    SPEEDING = "SPEEDING"
    RED_LIGHT = "RED_LIGHT"
    NO_SEATBELT = "NO_SEATBELT"
    PHONE_WHILE_DRIVING = "PHONE_WHILE_DRIVING"
    DRUNK_DRIVING = "DRUNK_DRIVING"
    INVALID_LICENCE = "INVALID_LICENCE"
    UNREGISTERED_VEHICLE = "UNREGISTERED_VEHICLE"
    NO_INSURANCE = "NO_INSURANCE"
    OVERLOADING = "OVERLOADING"
    RECKLESS_DRIVING = "RECKLESS_DRIVING"
    WRONG_LANE = "WRONG_LANE"
    OTHER = "OTHER"


class ViolationStatus(str, enum.Enum):
    UNPAID = "UNPAID"
    PAID = "PAID"
    DISPUTED = "DISPUTED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    WAIVED = "WAIVED"


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Challan reference — used as payment reference_id
    challan_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True,
        default=lambda: f"EC-{uuid.uuid4().hex[:8].upper()}"
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    driver_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Citizen NRC for direct lookup
    offender_nrc: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    plate_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)

    violation_type: Mapped[ViolationType] = mapped_column(Enum(ViolationType), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Officer who issued
    issued_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)

    # Fine — integer ngwee
    fine_amount_ngwee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    status: Mapped[ViolationStatus] = mapped_column(
        Enum(ViolationStatus), nullable=False, default=ViolationStatus.UNPAID, index=True
    )
    # Payment linkage — set when PAID
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Escalation tracking
    escalation_due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    points_deducted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    vehicle: Mapped["Vehicle | None"] = relationship("Vehicle", back_populates="violations")
    driver: Mapped["Driver | None"] = relationship("Driver", back_populates="violations")
