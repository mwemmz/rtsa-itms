import uuid
from datetime import datetime

from pydantic import BaseModel

from app.schemas.fields import LocationStr


class ANPREventCreate(BaseModel):
    plate_number: str
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

    model_config = {"from_attributes": True}
