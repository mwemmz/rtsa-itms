import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType
from app.models.enforcement import ViolationType

# What a member of the public can see well enough to report. Speeding isn't
# here: a fine for it needs a measured speed, which a bystander doesn't have.
CITIZEN_REPORTABLE = (
    ViolationType.RECKLESS_DRIVING,
    ViolationType.RUNNING_RED_LIGHT,
    ViolationType.USING_PHONE,
    ViolationType.ILLEGAL_PARKING,
    ViolationType.OVERLOADING,
    ViolationType.MISSING_NUMBER_PLATES,
    ViolationType.UNROADWORTHY,
    ViolationType.OTHER,
)


class DriverReportStatus(str, enum.Enum):
    PENDING = "pending"      # waiting for an officer
    ACCEPTED = "accepted"    # an officer turned it into a violation and fine
    REJECTED = "rejected"    # closed without action; nobody is blamed
    FALSE = "false"          # found untrue: the reporter is fined


class DriverReport(Base):
    """A citizen's report of someone else's driving, with the evidence for it.

    Nothing happens to the reported vehicle until an officer reviews the
    evidence and accepts it, which raises the violation and its fine through
    the same path as the officer's own form. Reports are tied to the
    reporter's NRC and a false one is fined, like a false road report.
    """

    __tablename__ = "driver_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUIDType, primary_key=True, default=uuid.uuid4)
    reporter_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=False, index=True
    )
    # The plate as the reporter gave it (tidied), and the vehicle it matched.
    plate_number: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True
    )
    # A ViolationType value; kept as text so this table doesn't share the
    # violations table's database enum type.
    violation_type: Mapped[str] = mapped_column(String(30), nullable=False)
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=DriverReportStatus.PENDING.value, index=True
    )
    review_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUIDType, ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # The violation an accepted report became.
    violation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("violations.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    @property
    def reference(self) -> str:
        return f"DR-{str(self.id)[:8].upper()}"
