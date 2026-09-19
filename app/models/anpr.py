"""ANPR (Automatic Number Plate Recognition) capture models."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class ANPRFlagReason(str, enum.Enum):
    NONE = "NONE"
    STOLEN = "STOLEN"
    BLACKLISTED = "BLACKLISTED"
    EXPIRED_INSURANCE = "EXPIRED_INSURANCE"
    EXPIRED_REGISTRATION = "EXPIRED_REGISTRATION"
    OUTSTANDING_FINES = "OUTSTANDING_FINES"
    WANTED = "WANTED"


class ANPRCapture(Base):
    __tablename__ = "anpr_captures"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vehicle_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    camera_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    image_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    confidence_score: Mapped[float | None] = mapped_column(nullable=True)

    # Compliance flags
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    flag_reason: Mapped[ANPRFlagReason] = mapped_column(
        Enum(ANPRFlagReason), nullable=False, default=ANPRFlagReason.NONE
    )
    flag_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    vehicle: Mapped["Vehicle | None"] = relationship("Vehicle", back_populates="anpr_captures")
