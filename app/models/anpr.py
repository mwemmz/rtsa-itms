import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class ANPREvent(Base):
    __tablename__ = "anpr_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    plate_number: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    camera_id: Mapped[str | None] = mapped_column(String(100), nullable=True)