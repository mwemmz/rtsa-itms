import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.models.driver_report import CITIZEN_REPORTABLE
from app.models.enforcement import ViolationType
from app.schemas.fields import LocationStr

# Old enough that nobody can still check it, and the vehicle may have changed hands.
REPORT_MAX_AGE = timedelta(days=30)
FUTURE_TOLERANCE = timedelta(minutes=5)

Plate = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=20)]


def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _web_link(value: str | None) -> str | None:
    value = (value or "").strip() or None
    if value and urlparse(value).scheme not in ("http", "https"):
        raise ValueError("The video link must be a web address starting with http:// or https://")
    return value


class DriverReportCreate(BaseModel):
    """What a citizen sends. Photos come alongside as files; see the endpoint."""

    plate_number: Plate
    violation_type: ViolationType
    location: LocationStr
    observed_at: datetime
    # What they saw, in their own words: the officer decides on it.
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=1000)]
    evidence_url: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    # "This is true to the best of my knowledge" -- a false report is fined.
    declaration: bool

    @field_validator("violation_type")
    @classmethod
    def _citizen_can_see_it(cls, value: ViolationType) -> ViolationType:
        if value not in CITIZEN_REPORTABLE:
            raise ValueError("That offence can't be reported by the public")
        return value

    @field_validator("evidence_url")
    @classmethod
    def _is_a_link(cls, value: str | None) -> str | None:
        return _web_link(value)

    @field_validator("declaration")
    @classmethod
    def _declared(cls, value: bool) -> bool:
        if not value:
            raise ValueError("Confirm the report is true to the best of your knowledge")
        return value

    @model_validator(mode="after")
    def _checks(self):
        when = _naive_utc(self.observed_at)
        now = datetime.utcnow()
        if when > now + FUTURE_TOLERANCE:
            raise ValueError("When it happened can't be in the future")
        if when < now - REPORT_MAX_AGE:
            raise ValueError("Reports must be made within 30 days of what happened")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send both latitude and longitude, or neither")
        self.plate_number = " ".join(self.plate_number.upper().split())
        return self


class ReportPhoto(BaseModel):
    id: uuid.UUID
    size_bytes: int
    sha256: str
    created_at: datetime | None = None


class DriverReportOut(BaseModel):
    id: uuid.UUID
    reference: str
    plate_number: str
    violation_type: str
    location: str
    latitude: float | None = None
    longitude: float | None = None
    gps_accuracy_m: float | None = None
    observed_at: datetime
    description: str
    evidence_url: str | None = None
    status: str
    review_note: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime | None = None
    photos: list[ReportPhoto] = []
    # For the officer reviewing it: who reported it and what the plate matched.
    reporter_name: str | None = None
    reporter_nrc: str | None = None
    reporter_false_reports: int | None = None
    vehicle: dict | None = None
    # Once accepted: the fine it became.
    challan_reference: str | None = None


class DriverReportAccept(BaseModel):
    """The officer may correct what the citizen got wrong before fining."""

    violation_type: ViolationType | None = None
    plate_number: Plate | None = None
    note: str | None = Field(default=None, max_length=300)


class DriverReportReject(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=300)]
    # True when the officer finds it untrue: the reporter is fined and it counts
    # towards a reporting suspension. Otherwise nobody is blamed.
    false_report: bool = False
