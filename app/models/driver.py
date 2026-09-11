import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class LicenceClass(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class DriverStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DISQUALIFIED = "disqualified"
    EXPIRED = "expired"


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True, index=True
    )
    licence_number: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    id_number: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    date_of_birth: Mapped[datetime] = mapped_column(nullable=False)
    phone_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    licence_class: Mapped[LicenceClass] = mapped_column(
        Enum(LicenceClass), nullable=False
    )
    licence_issue_date: Mapped[datetime] = mapped_column(nullable=False)
    licence_expiry_date: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[DriverStatus] = mapped_column(
        Enum(DriverStatus), default=DriverStatus.ACTIVE, nullable=False
    )
    restrictions: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )