"""One definition of "what is owed", shared by every screen and report.

Before this module each screen chose its own statuses, so the same citizen could be shown two fines on the
dashboard, three on a vehicle check and four on the fines page. The rules now are:

* **Owed** = status UNPAID or OVERDUE. This is what counts toward "fines to pay" and "amount due".
* **Overdue** = status OVERDUE, which is a subset of owed. The status is brought up to date (and the late
  surcharge added once) by ``ensure_current`` before any screen reads it, so a fine past its due date
  is never shown as merely "unpaid" in one place and "overdue" in another.
* **Under appeal** = status DISPUTED. Shown, but not owed while the appeal is decided.
* Paid and cancelled fines are history.
"""

from __future__ import annotations

import threading
import time

from sqlalchemy.orm import Session

from app.models.enforcement import ChallanStatus

OWED = (ChallanStatus.UNPAID, ChallanStatus.OVERDUE)
LISTED = (ChallanStatus.UNPAID, ChallanStatus.OVERDUE, ChallanStatus.DISPUTED)

_lock = threading.Lock()
_last = 0.0
_EVERY_S = 30.0


def ensure_current(db: Session, force: bool = False) -> None:
    """Mark fines past their due date as OVERDUE before they are counted. At most once every 30 s per worker."""
    global _last
    now = time.monotonic()
    with _lock:
        if not force and now - _last < _EVERY_S:
            return
        _last = now
    from app.services.overdue import mark_overdue_challans

    result = mark_overdue_challans(db)
    if result["challans_overdue"] or result["nominations_lapsed"]:
        db.commit()


def totals(challans) -> dict:
    """Counts and amounts for a list of challans, by the rules above."""
    owed = [c for c in challans if c.status in OWED]
    overdue = [c for c in owed if c.status == ChallanStatus.OVERDUE]
    return {
        "owed": len(owed),
        "overdue": len(overdue),
        "under_appeal": sum(1 for c in challans if c.status == ChallanStatus.DISPUTED),
        "amount_due": sum(c.penalty_amount for c in owed),
        "amount_overdue": sum(c.penalty_amount for c in overdue),
    }
