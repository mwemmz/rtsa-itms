"""Move e-Challans past their due date to OVERDUE and add the late surcharge once.

Nothing else sets the OVERDUE status, so without this a fine stays "unpaid" forever and the
admin setting ``enforcement.late_penalty_percent`` has no effect. Run hourly by the worker and
on demand from the admin System screen.
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.enforcement import Challan, ChallanStatus
from app.services import settings as runtime_settings
from app.services.audit import log_action


def mark_overdue_challans(db: Session, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.utcnow()
    percent = runtime_settings.get(db, "enforcement.late_penalty_percent")
    late = db.query(Challan).filter(Challan.status == ChallanStatus.UNPAID, Challan.due_date < now).all()
    added = 0
    for c in late:
        surcharge = round(c.penalty_amount * percent / 100)
        c.status = ChallanStatus.OVERDUE
        c.penalty_amount += surcharge
        added += surcharge
        log_action(db, "challan_overdue", "challan", str(c.id),
                   f"{c.reference} overdue; late surcharge {surcharge} ngwee ({percent}%)", None)
    from app.services.nomination import expire_nominations

    expired = expire_nominations(db, now)
    db.flush()
    return {"challans_overdue": len(late), "surcharge_ngwee": added, "nominations_lapsed": expired}
