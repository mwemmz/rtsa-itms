"""Citizen reports: who may report, and what a false report costs.

Two kinds of report share these rules: road reports (an accident, a closure)
and reports of a driver (dangerous driving, with photo or video evidence). A
false one of either kind is a strike, and both count towards the open limit.

A citizen's road report goes on the live feed - and closes its stretch in the
route planner - the moment it is made, marked unverified until an officer
reviews it. A driver report does nothing until an officer accepts it. What
keeps both honest is accountability:

* citizens sign up with their NRC number, so every report is traceable to a person;
* an officer who finds a report false fines the reporter's account (an e-Challan
  they settle like any other fine);
* ``road_reports.strike_limit`` false reports within ``strike_window_days``
  suspend reporting until the oldest of them ages out of the window;
* nobody can hold more than ``max_open_reports`` unverified reports at once, so
  one account can't close half the network before anyone looks.

All four numbers are admin settings (``app/services/settings.py``).
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.api.enforcement import create_challan_for_violation
from app.core.timeutil import aware, utcnow
from app.models.driver_report import DriverReport, DriverReportStatus
from app.models.enforcement import Challan, Violation, ViolationType
from app.models.road_network import IncidentVerification, RoadIncident
from app.models.user import STAFF_ROLES, User
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.notifications import notify


@dataclass
class Standing:
    can_report: bool
    reason: str | None
    nrc_on_file: bool
    open_reports: int
    max_open_reports: int
    false_reports: int
    strike_limit: int
    window_days: int
    fine_amount: int
    suspended_until: datetime | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def false_report_times(db: Session, user_id, since: datetime) -> list[datetime]:
    """When this user's reports (road or driver) were found false, newest first, back to ``since``."""
    rows = (
        db.query(RoadIncident.reviewed_at)
        .filter(
            RoadIncident.reported_by == user_id,
            RoadIncident.verification == IncidentVerification.FALSE.value,
            RoadIncident.reviewed_at.isnot(None),
        )
        .all()
    ) + (
        db.query(DriverReport.reviewed_at)
        .filter(
            DriverReport.reporter_id == user_id,
            DriverReport.status == DriverReportStatus.FALSE.value,
            DriverReport.reviewed_at.isnot(None),
        )
        .all()
    )
    times = [aware(r[0]) for r in rows]
    return sorted((t for t in times if t >= since), reverse=True)


def open_report_count(db: Session, user_id) -> int:
    road = (
        db.query(RoadIncident)
        .filter(
            RoadIncident.reported_by == user_id,
            RoadIncident.verification == IncidentVerification.UNVERIFIED.value,
            RoadIncident.is_active == True,  # noqa: E712
        )
        .count()
    )
    drivers = (
        db.query(DriverReport)
        .filter(DriverReport.reporter_id == user_id, DriverReport.status == DriverReportStatus.PENDING.value)
        .count()
    )
    return road + drivers


def reporter_standing(db: Session, user: User) -> Standing:
    limit = runtime_settings.get(db, "road_reports.strike_limit")
    window = runtime_settings.get(db, "road_reports.strike_window_days")
    max_open = runtime_settings.get(db, "road_reports.max_open_reports")
    fine = runtime_settings.get(db, "road_reports.false_report_fine")
    standing = Standing(
        can_report=True, reason=None, nrc_on_file=bool(user.nrc_number), open_reports=0,
        max_open_reports=max_open, false_reports=0, strike_limit=limit, window_days=window,
        fine_amount=fine,
    )
    if user.role in STAFF_ROLES:
        return standing  # staff reports are official; none of the citizen limits apply

    strikes = false_report_times(db, user.id, utcnow() - timedelta(days=window))
    standing.false_reports = len(strikes)
    standing.open_reports = open_report_count(db, user.id)

    if not user.nrc_number:
        standing.can_report = False
        standing.reason = "Add your NRC number under My account before reporting. Reports are tied to it."
    elif len(strikes) >= limit:
        # Lifts when the limit-th most recent false report leaves the window.
        standing.suspended_until = strikes[limit - 1] + timedelta(days=window)
        standing.can_report = False
        standing.reason = (
            f"Reporting is suspended until {standing.suspended_until:%d %b %Y}: "
            f"{len(strikes)} of your reports were found false in the last {window} days."
        )
    elif standing.open_reports >= max_open:
        standing.can_report = False
        standing.reason = (
            f"You have {standing.open_reports} reports waiting for an officer to review. "
            "You can report again once one has been reviewed."
        )
    return standing


def fine_false_report(db: Session, reporter: User, when: datetime, place: str, what: str,
                      officer: User) -> Challan:
    """Charge ``reporter`` the false-report fine: a violation on their account and its e-Challan."""
    violation = Violation(
        user_id=reporter.id,
        violation_type=ViolationType.FALSE_REPORT,
        location=place[:200],
        timestamp=when or datetime.utcnow(),
        description=what[:1000],
        recorded_by=officer.id,
        liable_party=ViolationType.FALSE_REPORT.liable_party,
    )
    db.add(violation)
    db.flush()
    log_action(db, "create", "violation", str(violation.id),
               f"False report by {reporter.full_name}", officer.id)
    return create_challan_for_violation(
        db, violation, officer.id,
        penalty_amount=runtime_settings.get(db, "road_reports.false_report_fine"),
        due_days=runtime_settings.get(db, "enforcement.challan_due_days"),
    )


def false_report_notice(db: Session, reporter: User, challan: Challan) -> dict:
    """The fine and strike count to tell a reporter whose report was found false."""
    standing = reporter_standing(db, reporter)
    return {
        "reference": challan.reference,
        "amount": challan.penalty_amount,
        "due_date": challan.due_date.strftime("%Y-%m-%d"),
        "strikes": standing.false_reports,
        "strike_limit": standing.strike_limit,
    }


def issue_false_report_fine(db: Session, incident: RoadIncident, place: str, officer: User) -> Challan | None:
    """Fine the reporter of a road report found false. None if there is nobody to charge."""
    reporter = db.get(User, incident.reported_by) if incident.reported_by else None
    if reporter is None:
        return None
    challan = fine_false_report(db, reporter, incident.starts_at, place,
                                f"False road report: {incident.review_note}", officer)
    notify(db, reporter.id, "road_report_false", {
        **false_report_notice(db, reporter, challan),
        "place": place,
        "reason": incident.review_note or "",
    })
    return challan
