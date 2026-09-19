"""Public Service Vehicle (PSV) permit models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class PSVPermitStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


class PSVRouteType(str, enum.Enum):
    URBAN = "URBAN"
    INTER_CITY = "INTER_CITY"
    RURAL = "RURAL"
    SCHOOL = "SCHOOL"
    TOUR = "TOUR"


class PSVPermit(Base):
    __tablename__ = "psv_permits"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    permit_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True,
        default=lambda: f"PSV-{uuid.uuid4().hex[:8].upper()}"
    )
    operator_name: Mapped[str] = mapped_column(String(255), nullable=False)
    operator_nrc: Mapped[str | None] = mapped_column(String(50), nullable=True, index=True)
    route_type: Mapped[PSVRouteType] = mapped_column(
        Enum(PSVRouteType), nullable=False, default=PSVRouteType.URBAN
    )
    route_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    passenger_capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[PSVPermitStatus] = mapped_column(
        Enum(PSVPermitStatus), nullable=False, default=PSVPermitStatus.ACTIVE, index=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    issued_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
