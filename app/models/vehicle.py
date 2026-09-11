import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class VehicleStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEREGISTERED = "deregistered"
    STOLEN = "stolen"


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True, index=True
    )
    registration_number: Mapped[str] = mapped_column(
        String(20), unique=True, index=True, nullable=False
    )
    owner_name: Mapped[str] = mapped_column(String(200), nullable=False)
    owner_id_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    make: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(nullable=False)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    engine_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    chassis_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus), default=VehicleStatus.ACTIVE, nullable=False
    )
    is_blacklisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    blacklist_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    registration_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )