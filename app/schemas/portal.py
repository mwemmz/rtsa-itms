import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.driver import DriverStatus, LicenceClass
from app.models.enforcement import ChallanStatus


class LicenceStatusOut(BaseModel):
    licence_number: str
    licence_class: LicenceClass
    status: DriverStatus
    issue_date: datetime
    expiry_date: datetime
    days_until_expiry: int
    state: str  # valid | expiring_soon | expired
    restrictions: str | None = None
    can_renew: bool = False
    renewal_opens: datetime | None = None
    demerit_points: int = 0
    demerit_limit: int = 12


class FineOut(BaseModel):
    id: uuid.UUID
    reference: str
    violation_type: str
    category: str = "both"
    liable_party: str = "both"
    location: str
    recorded_at: datetime
    # Which of the citizen's vehicles it was, when it was a vehicle.
    registration_number: str | None = None
    penalty_amount: int
    due_date: datetime
    status: ChallanStatus
    nomination_status: str = "none"
    nomination_deadline: datetime | None = None
    appeal_status: str | None = None
    can_appeal: bool = False


class AppealIn(BaseModel):
    reason: str
    details: str = Field(min_length=15, max_length=1000)


class AppealOut(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    challan_id: uuid.UUID
    reason: str
    details: str
    status: str
    decision_note: str | None = None
    created_at: datetime | None = None
    decided_at: datetime | None = None


class AppealDecisionIn(BaseModel):
    decision: str = Field(pattern="^(upheld|dismissed)$")
    note: str | None = Field(default=None, max_length=500)


class StolenReportIn(BaseModel):
    police_report_number: str | None = Field(default=None, max_length=60)  # optional: the system issues the reference
    stolen_at: datetime | None = None


class NominationOut(BaseModel):
    """A question put to the signed-in user about a fine: who was driving, or was it you."""

    id: uuid.UUID
    reference: str
    role: str  # "owner": name the driver. "driver": you were named.
    status: str
    registration_number: str | None = None
    violation_type: str
    location: str
    occurred_at: datetime
    penalty_amount: int
    deadline: datetime | None = None
    named_by: str | None = None
    statement: str | None = None


class NominateIn(BaseModel):
    licence_number: str = Field(min_length=3, max_length=40)
    statement: str | None = Field(default=None, max_length=500)


class NominationResponseIn(BaseModel):
    accept: bool
    note: str | None = Field(default=None, max_length=500)


class NominationDecisionIn(BaseModel):
    decision: str = Field(pattern="^(driver|owner)$")
    note: str | None = Field(default=None, max_length=500)


class PortalDashboard(BaseModel):
    licence: LicenceStatusOut | None
    vehicles: list[dict]
    outstanding_fines: list[FineOut]
    total_outstanding: int
    active_alerts: list[dict]
    unread_notifications: int


class RouteAlertOut(BaseModel):
    id: uuid.UUID
    incident_type: str
    severity: str
    description: str | None
    road_name: str | None
    stretch: str | None = None
    starts_at: datetime
    suggested_alternative: str | None = None
    # "unverified" while a citizen's report waits for an officer.
    verification: str = "official"