import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.toll import TollComplianceResult


class TollEventCreate(BaseModel):
    plate_number: str
    gate_id: str
    timestamp: datetime | None = None
    toll_amount: int | None = None


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
