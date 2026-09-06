"""The allocated-capital cap, and the check that keeps it honest (design §10).

The cap is the strongest risk control in the broker design -- stronger than the
per-trade buttons -- because it bounds the damage from *any* sizing defect to a
known number without needing to anticipate the defect. The zero-priced-holdings
bug made ₹47,075 invisible to sizing; a cap would have bounded that class
without knowing it existed.

It can only do that while the number is honest, and the way it stops being
honest is a field edit. Raising it without a matching ledger entry gives the
book money the unit count does not reflect, so **unit NAV jumps and reads as
performance that was never earned** -- the same class as the pre-inception
snapshots deleted as fabricated on 2026-09-05.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain.models import Holding, Lot
from algotrade.services import invariants
from algotrade.services.rebalance import RebalanceService

AS_OF = date(2026, 10, 1)


def _service() -> RebalanceService:
    return RebalanceService(entry=3, exit_=6, charge_reserve_bps=0)


def _holding(symbol: str, quantity: int, price="100.00") -> Holding:
    return Holding(portfolio_id="p", symbol=symbol,
                   lots=[Lot(lot_id=f"{symbol}:1", quantity=quantity,
                             price=Decimal(price),
                             acquired_on=date(2026, 9, 1))])


def _sized(cash="100000.00", allocated=None, holdings=None):
    return _service().size(
        target=["AAA"], holdings=holdings or [], cash=Decimal(cash),
        prices={"AAA": Decimal("100.00")},
        allocated=Decimal(allocated) if allocated else None)


# ----------------------------------------------------------- the ceiling ---

def test_without_a_cap_the_whole_book_is_investable():
    investable, _, _ = _sized()
    assert investable == Decimal("100000.00")


def test_a_cap_below_the_book_limits_what_is_deployed():
    """Bounds the damage from any sizing defect to a known number."""
    investable, _, _ = _sized(allocated="50000.00")
    assert investable == Decimal("50000.00")


def test_a_cap_above_the_book_does_not_top_it_up():
    """Applied as a ceiling, never a floor. A book worth less than its
    allocation is simply worth less; deploying the difference would spend
    money the ledger says is not there."""
    investable, _, _ = _sized(cash="30000.00", allocated="50000.00")
    assert investable == Decimal("30000.00")


def test_the_cap_counts_held_stock_not_only_cash():
    """A rebalance redeploys the whole book, so the limit has to apply to the
    whole book -- otherwise it caps idle cash and lets positions grow past it."""
    investable, _, _ = _sized(
        cash="0.00", allocated="50000.00",
        holdings=[_holding("AAA", 1000)])   # 1000 * 100 = 100,000
    assert investable == Decimal("50000.00")


def test_the_cap_binds_the_quantities_not_just_the_headline():
    """A limit that constrained a reported figure while sizing ignored it
    would be decoration."""
    _, _, capped = _sized(allocated="50000.00")
    _, _, uncapped = _sized()
    assert capped.quantities["AAA"] < uncapped.quantities["AAA"]


def test_a_cap_is_applied_before_the_charge_reserve():
    """The reserve is a fraction of what will be traded, so reserving against
    an amount the cap forbids would hold back money for trades that cannot
    happen."""
    service = RebalanceService(entry=3, exit_=6, charge_reserve_bps=100)
    investable, reserve, _ = service.size(
        target=["AAA"], holdings=[], cash=Decimal("100000.00"),
        prices={"AAA": Decimal("100.00")}, allocated=Decimal("50000.00"))
    assert reserve == Decimal("500.00")          # 100bps of 50,000, not 100,000
    assert investable == Decimal("49500.00")


# ------------------------------------------------------------ the check ---

def _ledger(*entries):
    return [{"kind": kind, "amount": Decimal(amount)}
            for kind, amount in entries]


def _check(allocated, ledger):
    return invariants.check_allocated_capital(
        "paper_momentum_v1_001",
        allocated=Decimal(allocated) if allocated else None, ledger=ledger)


def test_an_allocation_matching_its_funding_is_clean():
    got = _check("200000.00", _ledger(("opening_balance", "200000.00")))
    assert got.ok and got.checked == 1


def test_contributions_and_withdrawals_both_count():
    got = _check("210000.00", _ledger(("opening_balance", "200000.00"),
                                      ("contribution", "20000.00"),
                                      ("withdrawal", "-10000.00")))
    assert got.ok


def test_a_field_edit_without_a_ledger_entry_is_caught():
    """The failure this exists for: the book gains 10,000 with the unit count
    unchanged, so unit NAV jumps about 5% and reads as performance nobody
    earned."""
    got = _check("210000.00", _ledger(("opening_balance", "200000.00")))
    assert not got.ok
    assert got.findings[0].check == "allocated_capital"
    assert "field edit" in got.findings[0].detail


def test_trading_inside_the_book_does_not_change_the_allocation():
    """Buys, sells, charges and dividends move money WITHIN the book. Counting
    them would make the check fire on every ordinary rebalance."""
    got = _check("200000.00", _ledger(("opening_balance", "200000.00"),
                                      ("buy", "-50000.00"),
                                      ("sell", "48000.00"),
                                      ("charges", "-82.00"),
                                      ("dividend", "300.00")))
    assert got.ok


def test_a_portfolio_with_no_cap_is_not_checked():
    """Absent is a valid state. Reporting it nightly would train the reader to
    skim the one report that must stay worth reading."""
    got = _check(None, _ledger(("opening_balance", "200000.00")))
    assert got.ok and got.checked == 0


def test_an_allocation_with_no_funding_at_all_is_caught():
    got = _check("200000.00", [])
    assert not got.ok


def test_the_finding_states_both_numbers_and_the_gap():
    got = _check("210000.00", _ledger(("opening_balance", "200000.00")))
    detail = got.findings[0].detail
    assert "210000.00" in detail and "200000.00" in detail
    assert "10000.00" in detail
