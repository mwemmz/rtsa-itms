"""Import all models so Alembic autogenerate can detect every table."""

from app.models.base import Base  # noqa: F401

# ── Developer 2 — Citizen, Revenue, Integration, Security & Platform ──────
from app.models.citizen import (  # noqa: F401
    Application,
    ApplicationDocument,
    ApplicationStatus,
    ApplicationStatusHistory,
    ApplicationType,
    MfaChallenge,
    Role,
    User,
    UserRole,
    UserRoleAssignment,
    UserSession,
)
from app.models.payments import (  # noqa: F401
    IdempotencyRecord,
    PaymentIntent,
    PaymentRefund,
    PaymentStatus,
    PaymentTransaction,
    PaymentType,
    ReconciliationRun,
    ReconciliationStatus,
)
from app.models.notifications import (  # noqa: F401
    Notification,
    NotificationChannel,
    NotificationStatus,
    NotificationTemplate,
)
from app.models.admin import (  # noqa: F401
    ActorType,
    AuditLog,
    DataSharingContract,
    ExportJobStatus,
    IntegrationLog,
    ReportExportJob,
    SystemSetting,
    SystemThreshold,
)

# ── Developer 1 — Vehicle, Driver, Inspection, Insurance, Violations ──────
from app.models.vehicles import Vehicle, VehicleStatus, VehicleCategory  # noqa: F401
from app.models.drivers import Driver, LicenceClass, LicenceStatus       # noqa: F401
from app.models.inspections import Inspection, InspectionStatus, InspectionType  # noqa: F401
from app.models.insurance import InsurancePolicy, InsuranceStatus, InsuranceType  # noqa: F401
from app.models.violations import Violation, ViolationStatus, ViolationType  # noqa: F401
from app.models.accidents import Accident, AccidentSeverity, AccidentStatus  # noqa: F401
from app.models.anpr import ANPRCapture, ANPRFlagReason  # noqa: F401
from app.models.toll import TollPlaza, TollRecord, TollPaymentStatus, VehicleClass  # noqa: F401
from app.models.psv import PSVPermit, PSVPermitStatus, PSVRouteType  # noqa: F401
