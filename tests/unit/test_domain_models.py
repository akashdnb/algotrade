"""Tests for domain entities -- the invariants they carry, not their fields."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Fill,
    Holding,
    Item,
    ItemStatus,
    Lot,
    RecommendationSet,
    SetStatus,
    TaxStatus,
)

D = date
R = money.rupees


def _lot(qty=10, price="100.00", on=D(2026, 1, 1), charges="0.00", lot_id="L1") -> Lot:
    return Lot(lot_id=lot_id, acquired_on=on, quantity=qty, price=R(price),
               charges=R(charges))


# ------------------------------------------------------------------- lots ---


def test_lot_cost_basis_includes_charges():
    lot = _lot(qty=10, price="100.00", charges="15.34")
    assert lot.cost_basis == R("1015.34")


def test_a_lot_cannot_have_zero_or_negative_quantity():
    with pytest.raises(ValueError, match="quantity"):
        Lot(lot_id="L1", acquired_on=D(2026, 1, 1), quantity=0, price=R("100"))
    with pytest.raises(ValueError, match="quantity"):
        Lot(lot_id="L1", acquired_on=D(2026, 1, 1), quantity=-5, price=R("100"))


def test_tax_status_flips_strictly_after_365_days():
    """Exactly 365 days is still short-term: the statute says 'more than 12
    months', and rounding it the generous way understates tax owed."""
    lot = _lot(on=D(2026, 1, 1))
    assert lot.tax_status(D(2026, 12, 31)) == TaxStatus.SHORT_TERM
    assert lot.tax_status(D(2027, 1, 1)) == TaxStatus.SHORT_TERM    # exactly 365
    assert lot.tax_status(D(2027, 1, 2)) == TaxStatus.LONG_TERM     # 366


# --------------------------------------------------------------- holdings ---


def test_holding_quantity_and_basis_are_derived_from_lots():
    h = Holding(portfolio_id="paper_momentum_v1_001", symbol="TCS", lots=[
        _lot(qty=30, price="975.10", lot_id="L1"),
        _lot(qty=17, price="992.55", lot_id="L2"),
    ])
    assert h.quantity == 47
    assert h.cost_basis == R("46126.35")           # 29253.00 + 16873.35
    assert h.avg_cost == R("981.41")               # 46126.35 / 47


def test_an_empty_holding_has_no_average_cost_rather_than_dividing_by_zero():
    h = Holding(portfolio_id="p", symbol="TCS", lots=[])
    assert h.quantity == 0
    assert h.avg_cost == money.ZERO
    assert h.is_empty
    assert h.first_acquired is None


def test_holding_tax_status_follows_the_oldest_lot_because_fifo_sells_it_first():
    h = Holding(portfolio_id="p", symbol="TCS", lots=[
        _lot(on=D(2025, 1, 1), lot_id="old"),
        _lot(on=D(2026, 12, 1), lot_id="new"),
    ])
    assert h.tax_status(D(2026, 12, 15)) == TaxStatus.LONG_TERM


def test_unrealised_pnl_is_value_minus_basis():
    h = Holding(portfolio_id="p", symbol="TCS",
                lots=[_lot(qty=10, price="100.00", charges="20.00")])
    assert h.value_at(R("110.00")) == R("1100.00")
    assert h.unrealised_pnl(R("110.00")) == R("80.00")   # 1100 - 1020


# ------------------------------------------------------------------ items ---


def test_slippage_is_negative_when_a_sell_fills_below_reference():
    item = Item(seq=1, action=Action.SELL, symbol="X", quantity=10,
                reference_price=R("100.00"), executed_quantity=10,
                executed_price=R("99.00"), status=ItemStatus.EXECUTED)
    assert item.slippage_bps == pytest.approx(-100.0)


def test_slippage_is_negative_when_a_buy_fills_ABOVE_reference():
    """Both sides must read negative when they cost money. Reporting raw price
    change would make an expensive buy look like a +100 bps gain."""
    item = Item(seq=1, action=Action.BUY, symbol="X", quantity=10,
                reference_price=R("100.00"), executed_quantity=10,
                executed_price=R("101.00"), status=ItemStatus.EXECUTED)
    assert item.slippage_bps == pytest.approx(-100.0)


def test_a_favourable_buy_reads_positive():
    item = Item(seq=1, action=Action.BUY, symbol="X", quantity=10,
                reference_price=R("100.00"), executed_quantity=10,
                executed_price=R("99.00"), status=ItemStatus.EXECUTED)
    assert item.slippage_bps == pytest.approx(100.0)


def test_slippage_is_none_before_execution():
    item = Item(seq=1, action=Action.BUY, symbol="X", quantity=10,
                reference_price=R("100.00"))
    assert item.slippage_bps is None


# -------------------------------------------------------------------- sets ---


def _set(items: list[Item], status=SetStatus.PENDING) -> RecommendationSet:
    return RecommendationSet(
        set_id="p:2026-09:r1", portfolio_id="p", rebalance_run_id="p:2026-09",
        period="2026-09", as_of_session=D(2026, 9, 30), items=items, status=status,
    )


def _item(seq, status=ItemStatus.PENDING, action=Action.BUY, qty=10,
          ref="100.00", exec_price=None, exec_qty=0) -> Item:
    return Item(seq=seq, action=action, symbol=f"S{seq}", quantity=qty,
                reference_price=R(ref), status=status,
                executed_quantity=exec_qty,
                executed_price=R(exec_price) if exec_price else None)


def test_a_fully_executed_set_is_completed():
    s = _set([_item(1, ItemStatus.EXECUTED), _item(2, ItemStatus.EXECUTED)])
    assert s.derive_status() == SetStatus.COMPLETED


def test_executed_plus_skipped_still_counts_as_completed():
    """A skip is a settled decision, not outstanding work."""
    s = _set([_item(1, ItemStatus.EXECUTED), _item(2, ItemStatus.SKIPPED)])
    assert s.derive_status() == SetStatus.COMPLETED


def test_a_partly_worked_set_is_partially_executed():
    s = _set([_item(1, ItemStatus.EXECUTED), _item(2, ItemStatus.PENDING)])
    assert s.derive_status() == SetStatus.PARTIALLY_EXECUTED


def test_an_untouched_set_keeps_its_current_status():
    s = _set([_item(1), _item(2)], status=SetStatus.NOTIFIED)
    assert s.derive_status() == SetStatus.NOTIFIED


def test_superseded_and_expired_are_sticky_and_never_recomputed():
    """These are decisions about the SET. Recomputing them from item state
    would silently resurrect a set that was deliberately retired."""
    s = _set([_item(1, ItemStatus.EXECUTED)], status=SetStatus.SUPERSEDED)
    assert s.derive_status() == SetStatus.SUPERSEDED

    s2 = _set([_item(1, ItemStatus.EXECUTED)], status=SetStatus.EXPIRED)
    assert s2.derive_status() == SetStatus.EXPIRED


def test_realised_slippage_is_value_weighted_not_a_plain_average():
    """A ₹1,000 leg must not offset a ₹100,000 one."""
    big = _item(1, ItemStatus.EXECUTED, Action.BUY, qty=1000, ref="100.00",
                exec_price="100.10", exec_qty=1000)      # -10 bps on 100,100
    small = _item(2, ItemStatus.EXECUTED, Action.BUY, qty=10, ref="100.00",
                  exec_price="99.00", exec_qty=10)       # +100 bps on 990
    s = _set([big, small])
    weighted = s.realised_slippage_bps()
    plain = (big.slippage_bps + small.slippage_bps) / 2
    assert weighted == pytest.approx(-8.92, abs=0.05)
    assert plain == pytest.approx(45.0)                  # what NOT to report


def test_realised_slippage_is_none_when_nothing_executed():
    assert _set([_item(1), _item(2)]).realised_slippage_bps() is None


# ------------------------------------------------------------------- fills ---


def test_a_fill_needs_a_price_and_a_positive_quantity():
    with pytest.raises(ValueError, match="price"):
        Fill(seq=1, quantity=10)
    with pytest.raises(ValueError, match="quantity"):
        Fill(seq=1, quantity=0, price=R("100"))


def test_a_skip_needs_neither():
    f = Fill(seq=1, skip="insufficient funds after T+1")
    assert f.is_skip
    assert f.quantity == 0
