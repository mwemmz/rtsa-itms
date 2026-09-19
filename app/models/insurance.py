"""Vehicle insurance models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class InsuranceType(str, enum.Enum):
    THIRD_PARTY = "THIRD_PARTY"
    COMPREHENSIVE = "COMPREHENSIVE"
    THIRD_PARTY_FIRE_THEFT = "THIRD_PARTY_FIRE_THEFT"


class InsuranceStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUSPENDED = "SUSPENDED"


class InsurancePolicy(Base):
    __tablename__ = "insurance_policies"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    vehicle_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    policy_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    insurer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    insurance_type: Mapped[InsuranceType] = mapped_column(
        Enum(InsuranceType), nullable=False, default=InsuranceType.THIRD_PARTY
    )
    status: Mapped[InsuranceStatus] = mapped_column(
        Enum(InsuranceStatus), nullable=False, default=InsuranceStatus.ACTIVE, index=True
    )
    # Coverage period
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_to: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # Premium in ngwee
    premium_ngwee: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cover_amount_ngwee: Mapped[int | None] = mapped_column(Integer, nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="insurance_policies")
