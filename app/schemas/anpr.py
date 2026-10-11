import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.schemas.fields import LocationStr


class ANPREventCreate(BaseModel):
    # Same bound as the column; strip_whitespace so "   " isn't a plate.
    plate_number: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
    location: LocationStr
    timestamp: datetime | None = None
    confidence: float | None = None
    image_url: str | None = None
    camera_id: str | None = None


class ANPREventResponse(BaseModel):
    id: uuid.UUID
    plate_number: str
    vehicle_id: uuid.UUID | None
    location: str
    timestamp: datetime
    confidence: float | None
    image_url: str | None
    camera_id: str | None
    # "stop" when the vehicle is unregistered or fails a compliance check.
    decision: str | None = None
    reasons: list[str] = []

    model_config = {"from_attributes": True}
