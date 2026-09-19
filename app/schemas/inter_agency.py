"""Inter-agency integration schemas — section 17."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PoliceIncidentIn(BaseModel):
    incident_number: str
    incident_type: str
    occurred_at: datetime
    location: str | None = None
    vehicle_plate: str | None = None
    description: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class InsuranceVerifyRequest(BaseModel):
    vehicle_plate: str
    policy_number: str | None = None
    nrc_number: str | None = None


class InsuranceVerifyResponse(BaseModel):
    verified: bool
    policy_number: str | None
    insurer_name: str | None
    valid_from: str | None
    valid_to: str | None
    message: str | None


class HospitalAccidentIn(BaseModel):
    hospital_reference: str
    vehicle_plate: str | None = None
    patient_nrc: str | None = None
    accident_date: datetime
    injury_severity: str | None = None  # MINOR | SERIOUS | FATAL
    description: str | None = None


class NationalIdVerifyRequest(BaseModel):
    nrc_number: str
    full_name: str | None = None
    date_of_birth: str | None = None


class NationalIdVerifyResponse(BaseModel):
    verified: bool
    nrc_number: str
    full_name: str | None
    message: str | None


class IntegrationStatusEntry(BaseModel):
    agency: str
    last_success_at: datetime | None
    last_error_at: datetime | None
    error_rate_1h: float
    is_healthy: bool


class BatchIngestEvent(BaseModel):
    event_type: str
    payload: dict[str, Any]


class BatchIngestRequest(BaseModel):
    source_system: str
    events: list[BatchIngestEvent] = Field(max_length=500)


class BatchIngestResult(BaseModel):
    total: int
    accepted: int
    rejected: int
    errors: list[dict[str, Any]] = Field(default_factory=list)


class ContractOut(BaseModel):
    agency: str
    display_name: str
    schema_version: str
    allowed_fields: list[str]
    retention_days: int
    is_active: bool

    model_config = {"from_attributes": True}


class IntegrationLogOut(BaseModel):
    id: str
    agency: str
    direction: str
    endpoint: str
    status_code: int | None
    success: bool
    error_message: str | None
    latency_ms: int | None
    occurred_at: datetime

    model_config = {"from_attributes": True}
