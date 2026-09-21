import uuid
from datetime import datetime
from pydantic import Field

from pydantic import BaseModel

from app.models.toll import TollComplianceResult


class TollEventCreate(BaseModel):
    plate_number: str
    gate_id: str
    timestamp: datetime | None = None
    toll_amount: int | None = None


class OfflineTollEventCreate(BaseModel):
    device_event_id: str = Field(min_length=1, max_length=100)
    plate_number: str = Field(min_length=1, max_length=20)
    gate_id: str = Field(min_length=1, max_length=100)
    occurred_at: datetime
    toll_amount: int | None = None
    cached_compliance_result: TollComplianceResult | None = None
    cached_issues: list[str] = Field(default_factory=list)
    cached_checks: list[dict] = Field(default_factory=list)


class OfflineTollSyncRequest(BaseModel):
    event_ids: list[uuid.UUID] | None = None
    gate_id: str | None = None
    limit: int = Field(default=100, ge=1, le=500)


class OfflineTollSyncResponse(BaseModel):
    queued: int
    synced: int
    rejected: int
    transactions: list["TollTransactionResponse"]


class ComplianceCheckItem(BaseModel):
    check: str
    status: str
    detail: str | None = None


class TollTransactionResponse(BaseModel):
    id: uuid.UUID
    vehicle_id: uuid.UUID | None
    plate_number: str
    gate_id: str
    timestamp: datetime
    compliance_result: TollComplianceResult
    flagged_issues: str | None
    toll_amount: int | None
    is_paid: bool

    model_config = {"from_attributes": True}


class ComplianceResponse(BaseModel):
    vehicle_id: uuid.UUID | None
    plate_number: str
    compliance_result: TollComplianceResult
    checks: list[ComplianceCheckItem]
    flagged_issues: list[str]
    challan_created: bool = False
    transaction: TollTransactionResponse
