"""Vehicle registration models — covers Dev 1 section: Vehicle Registration & Management."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class VehicleStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    BLACKLISTED = "BLACKLISTED"
    DEREGISTERED = "DEREGISTERED"
    STOLEN = "STOLEN"


class VehicleCategory(str, enum.Enum):
    PRIVATE = "PRIVATE"
    COMMERCIAL = "COMMERCIAL"
    PSV = "PSV"
    GOVERNMENT = "GOVERNMENT"
    DIPLOMATIC = "DIPLOMATIC"


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Registration
    plate_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    chassis_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    engine_number: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # Owner — links to citizens.users by NRC
    owner_nrc: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    owner_name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    owner_address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Vehicle details
    make: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[VehicleCategory] = mapped_column(
        Enum(VehicleCategory), nullable=False, default=VehicleCategory.PRIVATE
    )
    seating_capacity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gross_weight_kg: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Status & compliance
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus), nullable=False, default=VehicleStatus.ACTIVE, index=True
    )
    registration_expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_roadworthy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    blacklist_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    # Relationships
    inspections: Mapped[list["Inspection"]] = relationship("Inspection", back_populates="vehicle")
    insurance_policies: Mapped[list["InsurancePolicy"]] = relationship(
        "InsurancePolicy", back_populates="vehicle"
    )
    violations: Mapped[list["Violation"]] = relationship("Violation", back_populates="vehicle")
    toll_records: Mapped[list["TollRecord"]] = relationship("TollRecord", back_populates="vehicle")
    anpr_captures: Mapped[list["ANPRCapture"]] = relationship("ANPRCapture", back_populates="vehicle")
