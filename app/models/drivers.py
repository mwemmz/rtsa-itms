"""Driver licensing models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class LicenceClass(str, enum.Enum):
    A = "A"   # Motorcycles
    B = "B"   # Light motor vehicles
    C = "C"   # Heavy motor vehicles
    D = "D"   # Buses / PSV
    EB = "EB" # Light motor vehicle (automatic)
    EC = "EC" # Heavy (automatic)


class LicenceStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    PENDING_RENEWAL = "PENDING_RENEWAL"


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    # Identity — links to citizens.users
    nrc_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date_of_birth: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Licence
    licence_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False, index=True)
    licence_class: Mapped[LicenceClass] = mapped_column(Enum(LicenceClass), nullable=False)
    licence_status: Mapped[LicenceStatus] = mapped_column(
        Enum(LicenceStatus), nullable=False, default=LicenceStatus.ACTIVE, index=True
    )
    issue_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expiry_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    # Demerit / restrictions
    demerit_points: Mapped[int] = mapped_column(default=0, nullable=False)
    restrictions: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array of restrictions

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    violations: Mapped[list["Violation"]] = relationship("Violation", back_populates="driver")
    accidents: Mapped[list["Accident"]] = relationship("Accident", back_populates="driver")
