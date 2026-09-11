import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class ViolationType(str, enum.Enum):
    SPEEDING = "speeding"
    RUNNING_RED_LIGHT = "running_red_light"
    NO_INSURANCE = "no_insurance"
    EXPIRED_FITNESS = "expired_fitness"
    NO_PSV_PERMIT = "no_psv_permit"
    DRIVING_WITHOUT_LICENCE = "driving_without_licence"
    ILLEGAL_PARKING = "illegal_parking"
    OVERLOADING = "overloading"
    REAR_SEAT_BELT = "rear_seat_belt"
    USING_PHONE = "using_phone"
    BLACKLISTED_VEHICLE = "blacklisted_vehicle"
    OTHER = "other"


class ChallanStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    OVERDUE = "overdue"
    DISPUTED = "disputed"


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("drivers.id"), nullable=True
    )
    violation_type: Mapped[ViolationType] = mapped_column(
        Enum(ViolationType), nullable=False
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Challan(Base):
    __tablename__ = "challans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    violation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("violations.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("drivers.id"), nullable=True
    )
    penalty_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[ChallanStatus] = mapped_column(
        Enum(ChallanStatus), default=ChallanStatus.UNPAID, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )