import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class PSVOperatorStatus(str, enum.Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"


class PSVPermitStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    SUSPENDED = "suspended"


class PSVOperator(Base):
    __tablename__ = "psv_operators"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    licence_number: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False
    )
    contact_person: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[PSVOperatorStatus] = mapped_column(
        Enum(PSVOperatorStatus), default=PSVOperatorStatus.ACTIVE, nullable=False
    )
    registered_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PSVPermit(Base):
    __tablename__ = "psv_permits"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("psv_operators.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=False, index=True
    )
    permit_number: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    route: Mapped[str] = mapped_column(String(200), nullable=False)
    issued_date: Mapped[datetime] = mapped_column(nullable=False)
    expiry_date: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[PSVPermitStatus] = mapped_column(
        Enum(PSVPermitStatus), default=PSVPermitStatus.ACTIVE, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )