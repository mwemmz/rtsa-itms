"""Appeals: a citizen contests an e-Challan; an officer upholds (the fine is cancelled) or dismisses it.

While an appeal is pending the fine is DISPUTED: it stops accruing the late surcharge and does not
flag the vehicle at toll gates. Dismissed, it goes back to how it was, with a week to pay.
"""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enforcement import Appeal, Challan, ChallanStatus
from app.models.user import User
from app.services import settings as runtime_settings
from app.services.audit import log_action
from app.services.demerits import void_points
from app.services.notifications import broadcast, notify
from app.services.ownership import owes_challan

REASONS = ("not_me", "wrong_vehicle", "wrong_details", "already_paid", "emergency", "other")
CAN_APPEAL = (ChallanStatus.UNPAID, ChallanStatus.OVERDUE)


def latest_appeal(db: Session, challan_id) -> Appeal | None:
    return db.query(Appeal).filter(Appeal.challan_id == challan_id).order_by(Appeal.created_at.desc()).first()


def appeal_window_open(db: Session, challan: Challan, now: datetime | None = None) -> bool:
    created = challan.created_at.replace(tzinfo=None) if challan.created_at and challan.created_at.tzinfo else challan.created_at
    days = runtime_settings.get(db, "enforcement.appeal_days")
    return created is None or (now or datetime.utcnow()) <= created + timedelta(days=days)


def file_appeal(db: Session, user: User, challan_id, reason: str, details: str) -> Appeal:
    challan = db.get(Challan, challan_id)
    if challan is None or not owes_challan(db, user, challan):
        raise HTTPException(status_code=404, detail="Fine not found")
    if challan.status not in CAN_APPEAL:
        raise HTTPException(status_code=409, detail="This fine can no longer be appealed")
    existing = latest_appeal(db, challan.id)
    if existing is not None and existing.status in ("pending", "dismissed"):
        raise HTTPException(status_code=409, detail="This fine has already been appealed")
    if not appeal_window_open(db, challan):
        raise HTTPException(status_code=409, detail="The time to appeal this fine has passed")
    appeal = Appeal(challan_id=challan.id, appellant_id=user.id, reason=reason, details=details.strip(),
                    prior_status=challan.status.value.upper())
    challan.status = ChallanStatus.DISPUTED
    db.add(appeal)
    db.flush()
    log_action(db, "appeal_filed", "challan", str(challan.id), f"{challan.reference} appealed: {reason}", user.id)
    broadcast(db, "appeal_received", {"reference": challan.reference, "appellant": user.full_name, "reason": reason.replace("_", " ")},
              roles=["officer", "admin"])
    return appeal


def decide_appeal(db: Session, officer: User, appeal_id, decision: str, note: str | None) -> Appeal:
    appeal = db.get(Appeal, appeal_id)
    if appeal is None:
        raise HTTPException(status_code=404, detail="Appeal not found")
    if appeal.status != "pending":
        raise HTTPException(status_code=409, detail="This appeal has already been decided")
    challan = db.get(Challan, appeal.challan_id)
    appeal.status = decision
    appeal.decided_by, appeal.decided_at, appeal.decision_note = officer.id, datetime.utcnow(), (note or "").strip()[:500] or None
    if decision == "upheld":
        challan.status = ChallanStatus.CANCELLED
        challan.nomination_status = "none"
        void_points(db, challan.violation_id, f"Fine {challan.reference} cancelled on appeal")
    else:
        challan.status = ChallanStatus[appeal.prior_status]
        challan.due_date = max(challan.due_date, datetime.utcnow() + timedelta(days=7))
    log_action(db, f"appeal_{decision}", "challan", str(challan.id), f"{challan.reference}: appeal {decision}", officer.id)
    notify(db, appeal.appellant_id, "appeal_decided",
           {"reference": challan.reference, "outcome": "upheld, so the fine is cancelled" if decision == "upheld" else "dismissed, so the fine stands (you have 7 more days to pay)",
            "note": appeal.decision_note or "No further comment."})
    return appeal


def withdraw_on_payment(db: Session, challan: Challan) -> None:
    """Paying a disputed fine ends the appeal."""
    appeal = latest_appeal(db, challan.id)
    if appeal is not None and appeal.status == "pending":
        appeal.status, appeal.decided_at = "withdrawn", datetime.utcnow()
