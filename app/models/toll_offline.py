import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class OfflineTollEventStatus(str, enum.Enum):
    QUEUED = "queued"
    SYNCED = "synced"
    REJECTED = "rejected"


class OfflineTollEvent(Base):
    __tablename__ = "toll_offline_events"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    device_event_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    plate_number: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    gate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    toll_amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_compliance_result: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cached_issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    cached_checks: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[OfflineTollEventStatus] = mapped_column(
        Enum(OfflineTollEventStatus), default=OfflineTollEventStatus.QUEUED, nullable=False, index=True
    )
    synced_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("toll_transactions.id"), nullable=True
    )
    sync_error: Mapped[str | None] = mapped_column(String(300), nullable=True)
    received_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, ForeignKey("users.id"), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)