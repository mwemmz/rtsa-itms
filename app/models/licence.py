import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.types import UUIDType
from app.models.driver import LicenceClass


class LicenceApplicationStatus(str, enum.Enum):
    SUBMITTED = "submitted"
    THEORY_TEST_SCHEDULED = "theory_test_scheduled"
    THEORY_TEST_PASSED = "theory_test_passed"
    THEORY_TEST_FAILED = "theory_test_failed"
    PRACTICAL_TEST_SCHEDULED = "practical_test_scheduled"
    PRACTICAL_TEST_PASSED = "practical_test_passed"
    PRACTICAL_TEST_FAILED = "practical_test_failed"
    ISSUED = "issued"
    REJECTED = "rejected"


class LicenceApplication(Base):
    __tablename__ = "licence_applications"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, primary_key=True, default=uuid.uuid4
    )
    applicant_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType, ForeignKey("users.id"), nullable=False, index=True
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    id_number: Mapped[str] = mapped_column(String(50), nullable=False)
    date_of_birth: Mapped[datetime] = mapped_column(nullable=False)
    requested_class: Mapped[LicenceClass] = mapped_column(
        Enum(LicenceClass), nullable=False
    )
    status: Mapped[LicenceApplicationStatus] = mapped_column(
        Enum(LicenceApplicationStatus), default=LicenceApplicationStatus.SUBMITTED, nullable=False
    )
    theory_score: Mapped[int | None] = mapped_column(nullable=True)
    practical_score: Mapped[int | None] = mapped_column(nullable=True)
    issued_licence_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )