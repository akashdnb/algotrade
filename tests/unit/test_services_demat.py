"""Tests for book-vs-demat reconciliation (design §11).

Each test names a real way the book and the demat can disagree. The one worth
reading twice is the series-change case: HFCL moving EQ->BE changed its symbol
while the position never moved, and matching on symbol would report it as both
missing and unexpected. A check that cries wolf on a corporate action is a
check people learn to ignore.
"""

from __future__ import annotations

from decimal import Decimal

from algotrade.brokers.base import BrokerHolding
from algotrade.services import demat

PORTFOLIO = "live_momentum_v1_001"
HFCL = "INE548A01028"
RELIANCE = "INE002A01018"


def _book(symbol: str, quantity: int, isin: str = HFCL) -> dict:
    return {"symbol": symbol, "isin": isin, "quantity": quantity}


def _held(symbol: str, quantity: int, isin: str = HFCL) -> BrokerHolding:
    return BrokerHolding(symbol=symbol, isin=isin, quantity=quantity,
                         average_price=Decimal("224.17"))


def _check(book, demat_rows):
    return demat.check_holdings(PORTFOLIO, book=book, demat=demat_rows)


# ---------------------------------------------------------- agreement ---

def test_matching_positions_produce_no_findings():
    report = _check([_book("HFCL", 30)], [_held("HFCL", 30)])
    assert report.ok
    assert report.checked == 1


def test_a_zero_quantity_book_row_is_not_checked():
    """A closed position is not a disagreement with a demat that lacks it."""
    report = _check([_book("HFCL", 0)], [])
    assert report.ok
    assert report.checked == 0


# ------------------------------------------------------- disagreement ---

def test_a_position_absent_from_the_demat_is_reported():
    """The shape of a sell that went through and was never recorded."""
    report = _check([_book("HFCL", 30)], [])
    assert not report.ok
    assert report.findings[0].check == "missing_at_broker"
    assert "book holds 30" in report.findings[0].detail


def test_a_quantity_mismatch_is_reported_with_both_numbers():
    """The shape of a partial fill recorded as complete."""
    report = _check([_book("HFCL", 30)], [_held("HFCL", 28)])
    finding = report.findings[0]
    assert finding.check == "quantity_mismatch"
    assert "book holds 30" in finding.detail
    assert "demat holds 28" in finding.detail


def test_a_position_only_the_broker_knows_about_is_reported():
    """A buy that filled and was never recorded, or a bonus issue."""
    report = _check([], [_held("HFCL", 30)])
    assert report.findings[0].check == "untracked_at_broker"
    assert "demat holds 30" in report.findings[0].detail


def test_both_directions_are_reported_together():
    report = _check([_book("RELIANCE", 3, RELIANCE)], [_held("HFCL", 30)])
    checks = {f.check for f in report.findings}
    assert checks == {"missing_at_broker", "untracked_at_broker"}


# ------------------------------------------------------ identity by ISIN ---

def test_a_symbol_change_with_the_same_isin_is_not_a_finding():
    """HFCL moving EQ->BE renames the scrip; the position never moved.

    Matching on symbol would report it as missing AND unexpected -- two
    findings for a corporate action that changed nothing.
    """
    report = _check([_book("HFCL", 30, HFCL)], [_held("HFCL-BE", 30, HFCL)])
    assert report.ok




def test_a_book_row_without_an_isin_falls_back_to_symbol():
    """The ordinary case, not an edge one: the current session format carries
    no ISIN and nothing on the recommendation path populates one, so
    ISIN-only matching would call every position unmatchable."""
    report = _check([_book("HFCL", 30, "")], [_held("HFCL", 30)])
    assert report.ok
    assert report.checked == 1


def test_a_symbol_only_mismatch_says_the_match_was_weak():
    """The weaker comparison must not pass for the stronger one."""
    report = _check([_book("HFCL", 30, "")], [_held("HFCL", 28, "")])
    assert report.findings[0].check == "quantity_mismatch"
    assert "matched on symbol" in report.findings[0].detail


def test_an_isin_match_is_not_labelled_as_weak():
    report = _check([_book("HFCL", 30)], [_held("HFCL", 28)])
    assert "matched on symbol" not in report.findings[0].detail


def test_a_broker_row_without_an_isin_still_satisfies_a_book_row():
    """Kite always reports ISIN, but a row that lacks one should still pair on
    symbol rather than manufacturing two spurious findings."""
    report = _check([_book("HFCL", 30)], [_held("HFCL", 30, "")])
    assert report.ok


def test_the_same_symbol_with_conflicting_isins_is_never_matched():
    """Same ticker, different scrip. Pairing them would compare two different
    companies' quantities and call the result a reconciliation."""
    report = _check([_book("XYZ", 30, HFCL)], [_held("XYZ", 30, RELIANCE)])
    checks = {f.check for f in report.findings}
    assert checks == {"missing_at_broker", "untracked_at_broker"}


# ------------------------------------------------------------- nothing ---

def test_a_zero_quantity_demat_row_is_ignored():
    """Kite reports sold-out holdings at zero on settlement day."""
    report = _check([], [_held("HFCL", 0)])
    assert report.ok
