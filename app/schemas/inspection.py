import uuid
from datetime import datetime

from pydantic import BaseModel, field_validator

from app.models.inspection import InspectionResult


class InspectionCreate(BaseModel):
    vehicle_id: uuid.UUID
    inspection_centre: str
    scheduled_date: datetime


class InspectionUpdate(BaseModel):
    result: InspectionResult | None = None
    findings: str | None = None
    inspected_by: str | None = None
    checklist: dict[str, str] | None = None
    readings: dict[str, float] | None = None
    obd_codes: list[str] | None = None


class ObdDecodeIn(BaseModel):
    codes: list[str]


class InspectionResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID
    inspection_centre: str
    scheduled_date: datetime
    result: InspectionResult
    findings: str | None
    inspected_by: str | None
    checklist: dict[str, str] = {}
    readings: dict[str, float] = {}
    obd_codes: list[str] = []
    created_at: datetime

    @field_validator("checklist", "readings", "obd_codes", mode="before")
    @classmethod
    def _parse(cls, v, info):
        import json

        empty = [] if info.field_name == "obd_codes" else {}
        if v is None:
            return empty
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return empty
        return v

    model_config = {"from_attributes": True}


class FitnessCertificateResponse(BaseModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    vehicle_id: uuid.UUID
    certificate_number: str
    issued_date: datetime
    expiry_date: datetime

    model_config = {"from_attributes": True}
