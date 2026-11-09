"""One rule for "what is owed" across every screen."""
from types import SimpleNamespace as NS

from app.models.enforcement import ChallanStatus as S
from app.services import fines


def _c(status, amount):
    return NS(status=status, penalty_amount=amount)


def test_totals_follow_the_single_rule():
    t = fines.totals([_c(S.UNPAID, 100), _c(S.OVERDUE, 200), _c(S.DISPUTED, 400), _c(S.PAID, 800), _c(S.CANCELLED, 50)])
    assert t == {"owed": 2, "overdue": 1, "under_appeal": 1, "amount_due": 300, "amount_overdue": 200}


def test_overdue_is_subset_of_owed_and_appeals_not_owed():
    assert S.OVERDUE in fines.OWED and S.DISPUTED not in fines.OWED and S.DISPUTED in fines.LISTED
