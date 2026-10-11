import uuid
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, Field, model_validator

from app.models.enforcement import ChallanStatus, ViolationType
from app.schemas.fields import LocationStr

# Allowance for a phone clock running a little ahead of the server's.
FUTURE_TOLERANCE = timedelta(minutes=5)
# Older than this, the officer must explain how the offence was observed.
BACKDATE_NEEDS_REASON = timedelta(hours=24)


class ViolationCreate(BaseModel):
    vehicle_id: uuid.UUID | None = None
    driver_id: uuid.UUID | None = None
    violation_type: ViolationType
    # A challan may be contested, so the location has to be on the record. A bare
    # `str` in pydantic v2 accepts "", which let a challan be raised with no place.
    location: LocationStr
    # Road speed when the offence was observed, km/h. Optional on the wire
    # because only some offences have a meaningful reading, but bounded so a
    # typo or a unit mix-up (metres, or a pasted odometer figure) cannot land
    # an absurd value on the record.
    speed_kmh: float | None = Field(default=None, ge=0, le=500)
    # Posted limit where the reading was taken, and the radar/camera that took it.
    speed_limit_kmh: float | None = Field(default=None, ge=5, le=200)
    speed_device: str | None = Field(default=None, max_length=100)
    # The officer's GPS fix, if their phone gave one.
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0, le=100000)
    timestamp: datetime | None = None
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _require_an_offender(self):
        # Neither field set means no one to charge and no one to notify --
        # the resulting challan would be uncollectable.
        if self.vehicle_id is None and self.driver_id is None:
            raise ValueError("A violation needs a vehicle_id, a driver_id, or both")
        return self

    @model_validator(mode="after")
    def _speeding_needs_its_evidence(self):
        # A speeding ticket that doesn't say how fast, or what the limit was,
        # can't be defended when the driver disputes it.
        if self.violation_type == ViolationType.SPEEDING:
            if self.speed_kmh is None or self.speed_limit_kmh is None:
                raise ValueError("A speeding violation needs the measured speed and the speed limit")
            if self.speed_kmh <= self.speed_limit_kmh:
                raise ValueError(
                    f"{self.speed_kmh:g} km/h is not over the {self.speed_limit_kmh:g} km/h limit"
                )
        if self.speed_device is not None:
            self.speed_device = self.speed_device.strip() or None
        return self

    @model_validator(mode="after")
    def _gps_fix_is_whole(self):
        # Half a coordinate is no location at all.
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send both latitude and longitude, or neither")
        if self.latitude is None:
            self.gps_accuracy_m = None
        return self

    @model_validator(mode="after")
    def _timestamp_is_believable(self):
        if self.timestamp is None:
            return self
        when = self.timestamp
        if when.tzinfo is not None:
            when = when.astimezone(timezone.utc).replace(tzinfo=None)
        now = datetime.utcnow()
        if when > now + FUTURE_TOLERANCE:
            raise ValueError("The offence time can't be in the future")
        # Recording an offence days later is legitimate (a citizen's video, a
        # camera review) but it's also how a ticket gets backdated, so the
        # officer has to say how it was observed.
        if when < now - BACKDATE_NEEDS_REASON and len((self.description or "").strip()) < 10:
            raise ValueError(
                "This offence is more than a day old. Say in the description how it was "
                "observed (at least 10 characters), e.g. 'citizen video, link: ...'"
            )
        return self


class ViolationResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    violation_type: ViolationType
    category: str = "both"
    liable_party: str = "driver"
    carries_licence_consequence: bool = False
    grounds_impoundment: bool = False
    location: str
    speed_kmh: float | None = None
    speed_limit_kmh: float | None = None
    speed_device: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    gps_accuracy_m: float | None = None
    timestamp: datetime
    description: str | None
    registration_number: str | None = None
    owner_name: str | None = None
    owner_id_number: str | None = None
    driver_name: str | None = None
    # Set when the violation is charged to an account rather than a vehicle or driver.
    account_name: str | None = None

    model_config = {"from_attributes": True}


class ChallanResponse(BaseModel):
    id: uuid.UUID
    reference: str
    violation_id: uuid.UUID
    violation_type: ViolationType | None = None
    category: str = "both"
    liable_party: str = "both"
    carries_licence_consequence: bool = False
    grounds_impoundment: bool = False
    vehicle_id: uuid.UUID | None
    driver_id: uuid.UUID | None
    penalty_amount: int
    due_date: datetime
    status: ChallanStatus
    created_at: datetime
    registration_number: str | None = None
    owner_name: str | None = None
    driver_name: str | None = None
    account_name: str | None = None

    model_config = {"from_attributes": True}


class ChallanPaymentResult(BaseModel):
    challan_id: uuid.UUID
    reference: str
    status: ChallanStatus
    message: str
