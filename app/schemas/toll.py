import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.toll import TollComplianceResult


class TollEventCreate(BaseModel):
    """A vehicle passing a gate, identified by plate (ANPR / manual), by FASTag, or both."""

    plate_number: str | None = Field(default=None, min_length=1, max_length=20)
    tag_id: str | None = Field(default=None, min_length=4, max_length=64)
    gate_id: str
    timestamp: datetime | None = None
    toll_amount: int | None = None
    # barrier: the vehicle stops and the gate opens or holds it.
    # mlff: multi-lane free flow, the vehicle never stops; the toll is billed to the owner.
    lane_mode: Literal["barrier", "mlff"] = "barrier"

    @model_validator(mode="after")
    def _needs_an_identifier(self):
        if not self.plate_number and not self.tag_id:
            raise ValueError("Give a plate_number, a tag_id, or both")
        return self


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
    identification_method: str = "anpr"
    lane_mode: str = "barrier"

    model_config = {"from_attributes": True}


class ComplianceResponse(BaseModel):
    vehicle_id: uuid.UUID | None
    plate_number: str
    compliance_result: TollComplianceResult
    checks: list[ComplianceCheckItem]
    flagged_issues: list[str]
    challan_created: bool = False
    # What the gate should do. In free-flow lanes nothing stops the vehicle, so a
    # flagged event is "dispatch_enforcement" (intercept it further along) instead of a held barrier.
    action: str = "proceed"
    barrier_required: bool = False
    transaction: TollTransactionResponse
