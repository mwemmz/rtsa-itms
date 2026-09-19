"""Toll plaza and toll record models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class TollPaymentStatus(str, enum.Enum):
    PAID = "PAID"
    UNPAID = "UNPAID"
    PENDING = "PENDING"
    WAIVED = "WAIVED"


class VehicleClass(str, enum.Enum):
    CLASS_A = "CLASS_A"   # Motorcycles / light
    CLASS_B = "CLASS_B"   # Passenger cars
    CLASS_C = "CLASS_C"   # Light commercial
    CLASS_D = "CLASS_D"   # Heavy commercial
    CLASS_E = "CLASS_E"   # Articulated trucks


class TollPlaza(Base):
    __tablename__ = "toll_plazas"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    plaza_code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    road: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    toll_records: Mapped[list["TollRecord"]] = relationship("TollRecord", back_populates="plaza")


class TollRecord(Base):
    __tablename__ = "toll_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plaza_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("toll_plazas.id", ondelete="SET NULL"), nullable=True, index=True
    )
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vehicle_class: Mapped[VehicleClass] = mapped_column(
        Enum(VehicleClass), nullable=False, default=VehicleClass.CLASS_B
    )
    transited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    amount_ngwee: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    payment_status: Mapped[TollPaymentStatus] = mapped_column(
        Enum(TollPaymentStatus), nullable=False, default=TollPaymentStatus.UNPAID, index=True
    )
    payment_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Offline sync flag
    synced_from_offline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_system: Mapped[str | None] = mapped_column(String(100), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vehicle: Mapped["Vehicle | None"] = relationship("Vehicle", back_populates="toll_records")
    plaza: Mapped["TollPlaza | None"] = relationship("TollPlaza", back_populates="toll_records")
