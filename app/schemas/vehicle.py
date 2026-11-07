import uuid
from datetime import datetime

import re
from pydantic import BaseModel, Field, field_validator

from app.models.vehicle import VehicleStatus


_PLATE_RE = re.compile(r"^[A-Z0-9][A-Z0-9 \-]{1,19}$")


class VehicleCreate(BaseModel):
    registration_number: str
    owner_name: str = Field(min_length=1, max_length=120)
    owner_id_number: str = Field(min_length=3, max_length=50)
    make: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int = Field(ge=1950)
    color: str | None = None
    engine_number: str | None = None
    chassis_number: str | None = None
    fastag_id: str | None = Field(default=None, min_length=4, max_length=64)

    @field_validator("registration_number")
    @classmethod
    def _plate(cls, v: str) -> str:
        v = " ".join(v.split()).upper()
        if not _PLATE_RE.match(v):
            raise ValueError("Registration number must be 2 to 20 letters, digits, spaces or hyphens (for example BAK 123)")
        return v

    @field_validator("year")
    @classmethod
    def _year(cls, v: int) -> int:
        from datetime import datetime as _dt
        if v > _dt.utcnow().year + 1:
            raise ValueError("Year cannot be in the future")
        return v

    @field_validator("engine_number", "chassis_number")
    @classmethod
    def _serial(cls, v: str | None) -> str | None:
        v = (v or "").strip().upper()
        return v or None


class VehicleUpdate(BaseModel):
    owner_name: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    color: str | None = None
    status: VehicleStatus | None = None
    is_blacklisted: bool | None = None
    blacklist_reason: str | None = None
    fastag_id: str | None = Field(default=None, min_length=4, max_length=64)


class VehicleResponse(BaseModel):
    id: uuid.UUID
    registration_number: str
    owner_name: str
    owner_id_number: str
    make: str
    model: str
    year: int
    color: str | None
    engine_number: str | None
    chassis_number: str | None
    fastag_id: str | None = None
    status: VehicleStatus
    is_blacklisted: bool
    blacklist_reason: str | None
    registration_date: datetime
    last_updated: datetime

    model_config = {"from_attributes": True}
