"""
Seed default system settings, thresholds and data-sharing contracts.
Idempotent — only inserts rows that don't already exist.
"""

import json

from sqlalchemy.orm import Session

from app.models.admin import DataSharingContract, SystemSetting, SystemThreshold

DEFAULT_SETTINGS = [
    {"key": "FINE_GRACE_PERIOD_DAYS", "value": json.dumps(7), "description": "Days after citation before fine escalates.", "requires_approval": False},
    {"key": "LICENCE_RENEWAL_REMINDER_DAYS", "value": json.dumps([30, 7]), "description": "Days before licence expiry to send reminders.", "requires_approval": False},
    {"key": "INSURANCE_RENEWAL_REMINDER_DAYS", "value": json.dumps([30, 7]), "description": "Days before insurance expiry to send reminders.", "requires_approval": False},
    {"key": "INSPECTION_REMINDER_DAYS", "value": json.dumps(14), "description": "Days before inspection due date to remind.", "requires_approval": False},
    {"key": "TOLL_RATE_CLASS_A_NGWEE", "value": json.dumps(5000), "description": "Toll rate for Class A vehicles in ngwee.", "requires_approval": True},
    {"key": "TOLL_RATE_CLASS_B_NGWEE", "value": json.dumps(10000), "description": "Toll rate for Class B vehicles in ngwee.", "requires_approval": True},
    {"key": "PSV_PERMIT_FEE_NGWEE", "value": json.dumps(50000), "description": "PSV permit fee in ngwee.", "requires_approval": True},
    {"key": "INSPECTION_FEE_NGWEE", "value": json.dumps(15000), "description": "Vehicle inspection fee in ngwee.", "requires_approval": True},
]

DEFAULT_THRESHOLDS = [
    {"key": "BRUTE_FORCE_MAX_ATTEMPTS", "value": json.dumps(5), "description": "Failed login attempts before account lockout.", "unit": "count"},
    {"key": "BRUTE_FORCE_LOCKOUT_MINUTES", "value": json.dumps(15), "description": "Duration of brute-force lockout.", "unit": "minutes"},
    {"key": "TOLL_CHECK_SLA_MS", "value": json.dumps(500), "description": "Maximum acceptable toll compliance check latency.", "unit": "ms"},
    {"key": "FINE_ESCALATION_DAYS", "value": json.dumps(30), "description": "Days after issuance before fine escalates to court.", "unit": "days"},
    {"key": "JWT_ACCESS_TOKEN_MINUTES", "value": json.dumps(15), "description": "JWT access token lifetime.", "unit": "minutes"},
    {"key": "REFRESH_TOKEN_DAYS", "value": json.dumps(7), "description": "Refresh token lifetime.", "unit": "days"},
    {"key": "RATE_LIMIT_CITIZEN_RPM", "value": json.dumps(60), "description": "Citizen portal rate limit (requests/minute).", "unit": "req/min"},
    {"key": "RATE_LIMIT_SERVICE_RPM", "value": json.dumps(600), "description": "Service-to-service rate limit (requests/minute).", "unit": "req/min"},
    {"key": "REPORT_ASYNC_ROW_THRESHOLD", "value": json.dumps(10000), "description": "Row count above which reports are forced async.", "unit": "rows"},
    {"key": "IDEMPOTENCY_TTL_HOURS", "value": json.dumps(24), "description": "Idempotency key TTL.", "unit": "hours"},
]

DEFAULT_CONTRACTS = [
    {
        "agency": "POLICE",
        "display_name": "Zambia Police Service",
        "schema_version": "1.0",
        "allowed_fields": json.dumps(["incident_number", "incident_type", "occurred_at", "vehicle_plate", "location"]),
        "retention_days": 2555,  # 7 years
        "is_active": True,
        "notes": "Incident data and vehicle lookups.",
    },
    {
        "agency": "INSURANCE",
        "display_name": "Insurance Association of Zambia",
        "schema_version": "1.0",
        "allowed_fields": json.dumps(["policy_number", "vehicle_plate", "valid_from", "valid_to", "insurer_name"]),
        "retention_days": 365,
        "is_active": True,
        "notes": "Real-time policy verification gateway.",
    },
    {
        "agency": "HOSPITAL",
        "display_name": "Ministry of Health — Accident Reporting",
        "schema_version": "1.0",
        "allowed_fields": json.dumps(["hospital_reference", "vehicle_plate", "accident_date", "injury_severity"]),
        "retention_days": 2555,
        "is_active": True,
        "notes": "Inbound accident/casualty notifications from hospitals.",
    },
    {
        "agency": "NATIONAL_ID",
        "display_name": "Department of National Registration",
        "schema_version": "1.0",
        "allowed_fields": json.dumps(["nrc_number", "full_name", "date_of_birth"]),
        "retention_days": 0,  # no retention — query only
        "is_active": True,
        "notes": "NRC verification. No data stored — query only.",
    },
    {
        "agency": "TOLL",
        "display_name": "National Roads Fund Agency — Toll Plazas",
        "schema_version": "1.0",
        "allowed_fields": json.dumps(["plate", "plaza_id", "transited_at", "amount_ngwee", "vehicle_class"]),
        "retention_days": 365,
        "is_active": True,
        "notes": "Offline toll-plaza batch sync.",
    },
]


def seed_defaults(db: Session) -> None:
    """Idempotent seed — only inserts rows that don't exist yet."""
    for s in DEFAULT_SETTINGS:
        if not db.query(SystemSetting).filter(SystemSetting.key == s["key"]).first():
            db.add(SystemSetting(**s))

    for t in DEFAULT_THRESHOLDS:
        if not db.query(SystemThreshold).filter(SystemThreshold.key == t["key"]).first():
            db.add(SystemThreshold(**t))

    for c in DEFAULT_CONTRACTS:
        if not db.query(DataSharingContract).filter(DataSharingContract.agency == c["agency"]).first():
            db.add(DataSharingContract(**c))

    db.commit()
