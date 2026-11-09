import uuid
from datetime import datetime

from pydantic import field_validator, model_validator, BaseModel, Field

from app.schemas import validators as V
from app.models.psv import PSVOperatorStatus, PSVPermitStatus


class PSVOperatorCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    licence_number: str = Field(min_length=2, max_length=50)
    contact_person: str = Field(min_length=1, max_length=120)
    phone: str
    email: str | None = None
    address: str | None = Field(default=None, max_length=300)

    @field_validator("name", "licence_number", "address")
    @classmethod
    def _txt(cls, v):
        return V.clean(v, "This field", required=False) if v is not None else v

    @field_validator("contact_person")
    @classmethod
    def _contact(cls, v: str) -> str:
        return V.person_name(v, "Contact person")

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        out = V.phone(v)
        if not out:
            raise ValueError("Phone number is required")
        return out

    @field_validator("email")
    @classmethod
    def _email(cls, v):
        return V.email(v, required=False)


class PSVOperatorResponse(BaseModel):
    id: uuid.UUID
    name: str
    licence_number: str
    contact_person: str
    phone: str
    email: str | None
    address: str | None
    status: PSVOperatorStatus
    registered_date: datetime

    model_config = {"from_attributes": True}


def _naive_utc(v):
    """A timestamp sent with a timezone (Z or +02:00) becomes naive UTC, like the rest of the database."""
    if getattr(v, "tzinfo", None) is not None:
        from datetime import timezone

        return v.astimezone(timezone.utc).replace(tzinfo=None)
    return v


class PSVPermitCreate(BaseModel):
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime

    @field_validator("issued_date", "expiry_date")
    @classmethod
    def _utc(cls, v):
        return _naive_utc(v)

    @model_validator(mode="after")
    def _expiry_after_issue(self):
        if self.expiry_date <= self.issued_date:
            raise ValueError("Permit expiry must be after the issue date")
        return self


class PSVPermitResponse(BaseModel):
    id: uuid.UUID
    operator_id: uuid.UUID
    vehicle_id: uuid.UUID
    permit_number: str
    route: str
    issued_date: datetime
    expiry_date: datetime
    status: PSVPermitStatus

    model_config = {"from_attributes": True}

class Waypoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class PSVCorridorSet(BaseModel):
    waypoints: list[Waypoint] = Field(min_length=2, max_length=200)
    buffer_m: int = Field(default=500, ge=50, le=20000)


class PSVCorridorResponse(BaseModel):
    permit_id: uuid.UUID
    waypoints: list[Waypoint]
    buffer_m: int


class PSVPositionReport(BaseModel):
    plate_number: str = Field(min_length=1, max_length=20)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timestamp: datetime | None = None


class PSVPositionResult(BaseModel):
    plate_number: str
    permit_number: str
    inside: bool
    distance_m: int
    buffer_m: int
    alert_id: uuid.UUID | None = None
    alert_deduplicated: bool = False


class PSVRouteAlertResponse(BaseModel):
    id: uuid.UUID
    permit_id: uuid.UUID
    vehicle_id: uuid.UUID
    latitude: float
    longitude: float
    distance_m: int
    buffer_m: int
    occurred_at: datetime
    acknowledged: bool

    model_config = {"from_attributes": True}
