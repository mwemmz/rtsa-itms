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
    created_at: datetime

    @field_validator("checklist", "readings", mode="before")
    @classmethod
    def _parse(cls, v):
        from app.services.inspection_rules import load

        return load(v) if isinstance(v, str) or v is None else v

    model_config = {"from_attributes": True}


class FitnessCertificateResponse(BaseModel):
    id: uuid.UUID
    inspection_id: uuid.UUID
    vehicle_id: uuid.UUID
    certificate_number: str
    issued_date: datetime
    expiry_date: datetime

    model_config = {"from_attributes": True}
