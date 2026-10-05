import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType


class ViolationType(str, enum.Enum):
    """Traffic offence taxonomy.

    Offences split along two axes that RTSA treats differently:

    * **Driver** — how a person drives, or their fitness and authority to
      drive. Attaches to the individual behind the wheel and carries licence
      consequences: points, endorsement, suspension or disqualification, and
      in serious cases imprisonment.
    * **Vehicle** — the condition, registration or documentation of the
      vehicle itself. Responsibility falls on the registered owner or keeper
      regardless of who was driving, the penalty is a fine, and the vehicle
      can be impounded or taken off the road.
    * **Both** — offences that can implicate the driver and the owner at
      once, e.g. driving a vehicle you know is unroadworthy.
    """

    # --- Driver: conduct and fitness to drive ---
    SPEEDING = "speeding"
    RUNNING_RED_LIGHT = "running_red_light"
    USING_PHONE = "using_phone"
    REAR_SEAT_BELT = "rear_seat_belt"
    DRIVING_WITHOUT_LICENCE = "driving_without_licence"
    DRUNK_DRIVING = "drunk_driving"
    RECKLESS_DRIVING = "reckless_driving"

    # --- Vehicle: condition, registration, documentation ---
    EXPIRED_FITNESS = "expired_fitness"
    UNROADWORTHY = "unroadworthy"
    EXPIRED_ROAD_TAX = "expired_road_tax"
    MISSING_NUMBER_PLATES = "missing_number_plates"
    ILLEGAL_MODIFICATION = "illegal_modification"
    OVERLOADING = "overloading"
    NO_PSV_PERMIT = "no_psv_permit"
    BLACKLISTED_VEHICLE = "blacklisted_vehicle"

    # --- Overlap: implicates driver and owner ---
    NO_INSURANCE = "no_insurance"
    ILLEGAL_PARKING = "illegal_parking"
    OTHER = "other"

    @property
    def category(self) -> str:
        if self in DRIVER_OFFENCES:
            return "driver"
        if self in VEHICLE_OFFENCES:
            return "vehicle"
        return "both"

    @property
    def carries_licence_consequence(self) -> bool:
        """Whether the offence can add licence points or lead to a ban."""
        return self in DRIVER_OFFENCES

    @property
    def grounds_impoundment(self) -> bool:
        """Whether the vehicle can be impounded or taken off the road."""
        return self in VEHICLE_OFFENCES

    @property
    def liable_party(self) -> str:
        """Who carries the penalty: the driver, the registered owner, or both.

        Vehicle offences fall on the registered owner or keeper regardless of
        who was driving, so they resolve to ``owner`` rather than ``vehicle``.
        """
        if self.category == "both":
            return "both"
        return "driver" if self in DRIVER_OFFENCES else "owner"


class ChallanStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PAID = "paid"
    OVERDUE = "overdue"
    DISPUTED = "disputed"


# Resolved after the enum body so the properties above can reference them.
DRIVER_OFFENCES = frozenset({
    ViolationType.SPEEDING,
    ViolationType.RUNNING_RED_LIGHT,
    ViolationType.USING_PHONE,
    ViolationType.REAR_SEAT_BELT,
    ViolationType.DRIVING_WITHOUT_LICENCE,
    ViolationType.DRUNK_DRIVING,
    ViolationType.RECKLESS_DRIVING,
})

VEHICLE_OFFENCES = frozenset({
    ViolationType.EXPIRED_FITNESS,
    ViolationType.UNROADWORTHY,
    ViolationType.EXPIRED_ROAD_TAX,
    ViolationType.MISSING_NUMBER_PLATES,
    ViolationType.ILLEGAL_MODIFICATION,
    ViolationType.OVERLOADING,
    ViolationType.NO_PSV_PERMIT,
    ViolationType.BLACKLISTED_VEHICLE,
})

BOTH_OFFENCES = frozenset({
    ViolationType.NO_INSURANCE,
    ViolationType.ILLEGAL_PARKING,
    ViolationType.OTHER,
})


class Violation(Base):
    __tablename__ = "violations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("drivers.id"), nullable=True
    )
    violation_type: Mapped[ViolationType] = mapped_column(
        Enum(ViolationType), nullable=False
    )
    location: Mapped[str] = mapped_column(String(200), nullable=False)
    # Measured road speed at the moment the offence was observed, km/h. Optional
    # because most offences have no meaningful speed reading, but a speeding
    # ticket is worthless without it, so the form prompts for it on those.
    speed_kmh: Mapped[float | None] = mapped_column(Float, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=True
    )
    liable_party: Mapped[str] = mapped_column(
        String(10), nullable=False, default="driver", server_default="driver"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    @property
    def category(self) -> str:
        return self.violation_type.category

    @property
    def carries_licence_consequence(self) -> bool:
        return self.violation_type.carries_licence_consequence

    @property
    def grounds_impoundment(self) -> bool:
        return self.violation_type.grounds_impoundment


class Challan(Base):
    __tablename__ = "challans"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    reference: Mapped[str] = mapped_column(
        String(50), unique=True, index=True, nullable=False
    )
    violation_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("violations.id"), nullable=False, index=True
    )
    vehicle_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("vehicles.id"), nullable=True, index=True
    )
    driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType, ForeignKey("drivers.id"), nullable=True
    )
    penalty_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    due_date: Mapped[datetime] = mapped_column(nullable=False)
    status: Mapped[ChallanStatus] = mapped_column(
        Enum(ChallanStatus), default=ChallanStatus.UNPAID, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )