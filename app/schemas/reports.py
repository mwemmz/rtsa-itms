"""Reporting & analytics schemas — section 16."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DashboardKPI(BaseModel):
    total_registered_vehicles: int
    total_licensed_drivers: int
    total_violations_ytd: int
    total_accidents_ytd: int
    revenue_collected_ngwee: int
    pending_applications: int
    active_psv_vehicles: int
    toll_transactions_today: int


class ReportDefinition(BaseModel):
    report_type: str
    display_name: str
    description: str
    available_filters: list[str]
    required_permission: str


class ReportDataResponse(BaseModel):
    report_type: str
    generated_at: datetime
    filters_applied: dict[str, Any]
    columns: list[str]
    rows: list[dict[str, Any]]
    total_rows: int
