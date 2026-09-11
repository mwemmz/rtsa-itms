import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class TollComplianceResult(str, enum.Enum):
    COMPLIANT = "compliant"
    FLAGGED = "flagged"


class TollTransaction(Base):
    __tablename__ = "toll_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    plate_number: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    gate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    compliance_result: Mapped[TollComplianceResult] = mapped_column(
        Enum(TollComplianceResult), nullable=False
    )
    flagged_issues: Mapped[str | None] = mapped_column(Text, nullable=True)
    toll_amount: Mapped[int | None] = mapped_column(nullable=True)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )