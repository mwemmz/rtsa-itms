import uuid
from datetime import datetime

from pydantic import BaseModel

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


class FineOut(BaseModel):
    id: uuid.UUID
    reference: str
    violation_type: str
    location: str
    recorded_at: datetime
    penalty_amount: int
    due_date: datetime
    status: ChallanStatus


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
    starts_at: datetime
    suggested_alternative: str | None = None