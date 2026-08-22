"""Tests for FIFO lot matching and STCG/LTCG classification.

§11q established the STCG (20%) / LTCG (12.5%) asymmetry is large enough to
reverse conclusions, and that a wrong cost basis produced a 36% effective rate
on a 20% gain. This module is where that arithmetic lives, so it is tested
against hand-computed cases rather than against itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain import money, tax
from algotrade.domain.models import Holding, Lot, TaxStatus

D = date
R = money.rupees


def _h(lots: list[Lot]) -> Holding:
    return Holding(portfolio_id="p", symbol="TCS", lots=lots)


def _lot(lot_id, qty, price, on, charges="0.00") -> Lot:
    return Lot(lot_id=lot_id, acquired_on=on, quantity=qty, price=R(price),
               charges=R(charges))


# ------------------------------------------------------------ consumption ---


def test_selling_less_than_the_oldest_lot_partially_consumes_it():
    h = _h([_lot("L1", 30, "100.00", D(2026, 1, 1))])
    result = tax.sell_fifo(h, quantity=10, price=R("110.00"), on=D(2026, 6, 1))

    assert len(result.matches) == 1
    assert result.matches[0].quantity == 10
    assert result.matches[0].lot_id == "L1"
    assert result.remaining_lots[0].quantity == 20        # 30 - 10
    assert result.remaining_lots[0].lot_id == "L1"


def test_selling_spans_lots_oldest_first():
    h = _h([
        _lot("L1", 10, "100.00", D(2026, 1, 1)),
        _lot("L2", 10, "200.00", D(2026, 6, 1)),
    ])
    result = tax.sell_fifo(h, quantity=15, price=R("300.00"), on=D(2026, 9, 1))

    assert [(m.lot_id, m.quantity) for m in result.matches] == [("L1", 10), ("L2", 5)]
    assert len(result.remaining_lots) == 1
    assert result.remaining_lots[0].lot_id == "L2"
    assert result.remaining_lots[0].quantity == 5


def test_lots_are_consumed_by_acquisition_date_not_list_order():
    """FIFO is a property of the dates, not of however the list was built."""
    h = _h([
        _lot("NEW", 10, "200.00", D(2026, 6, 1)),
        _lot("OLD", 10, "100.00", D(2026, 1, 1)),
    ])
    result = tax.sell_fifo(h, quantity=10, price=R("300.00"), on=D(2026, 9, 1))
    assert result.matches[0].lot_id == "OLD"


def test_selling_the_whole_position_leaves_no_lots():
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1))])
    result = tax.sell_fifo(h, quantity=10, price=R("110.00"), on=D(2026, 6, 1))
    assert result.remaining_lots == []


def test_overselling_is_refused_rather_than_silently_clamped():
    """Clamping would book a phantom sale and leave the book internally
    inconsistent -- the error must surface at the point of the mistake."""
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1))])
    with pytest.raises(tax.InsufficientQuantity, match="10"):
        tax.sell_fifo(h, quantity=11, price=R("110.00"), on=D(2026, 6, 1))


# -------------------------------------------------------------- proceeds ----


def test_gain_is_net_of_both_buy_and_sell_charges():
    """Buy charges are in the basis; sell charges reduce proceeds. Omitting
    either overstates the gain and therefore the tax."""
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1), charges="15.34")])
    result = tax.sell_fifo(h, quantity=10, price=R("110.00"), on=D(2026, 6, 1),
                           charges=R("16.00"))

    assert result.proceeds == R("1100.00")
    assert result.cost_basis == R("1015.34")        # 1000 + 15.34 buy charges
    assert result.gain == R("68.66")                # 1100 - 1015.34 - 16.00


def test_partial_consumption_apportions_the_lots_buy_charges():
    """Selling half a lot carries half its charges into the basis. Charging
    the whole lot's fee against a partial sale overstates the basis and
    understates the gain."""
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1), charges="20.00")])
    result = tax.sell_fifo(h, quantity=5, price=R("110.00"), on=D(2026, 6, 1))

    assert result.cost_basis == R("510.00")         # 500 + half of 20
    assert result.gain == R("40.00")                # 550 - 510


def test_a_loss_is_reported_as_a_negative_gain():
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1))])
    result = tax.sell_fifo(h, quantity=10, price=R("90.00"), on=D(2026, 6, 1))
    assert result.gain == R("-100.00")


# ----------------------------------------------------- tax classification ---


def test_each_match_carries_its_own_tax_status():
    """One sale can straddle the 12-month boundary: the old lot is LTCG and the
    new one STCG, at different rates, within a single transaction."""
    h = _h([
        _lot("OLD", 10, "100.00", D(2025, 1, 1)),     # > 365 days by sell date
        _lot("NEW", 10, "100.00", D(2026, 9, 1)),
    ])
    result = tax.sell_fifo(h, quantity=15, price=R("150.00"), on=D(2026, 10, 1))

    by_lot = {m.lot_id: m for m in result.matches}
    assert by_lot["OLD"].tax_status == TaxStatus.LONG_TERM
    assert by_lot["NEW"].tax_status == TaxStatus.SHORT_TERM


def test_estimated_tax_applies_the_right_rate_to_each_slice():
    h = _h([
        _lot("OLD", 10, "100.00", D(2025, 1, 1)),
        _lot("NEW", 10, "100.00", D(2026, 9, 1)),
    ])
    result = tax.sell_fifo(h, quantity=20, price=R("200.00"), on=D(2026, 10, 1))

    # Each slice: 10 x (200 - 100) = 1,000 gain.
    assert result.long_term_gain == R("1000.00")
    assert result.short_term_gain == R("1000.00")
    # 1,000 x 12.5% + 1,000 x 20% = 125 + 200
    assert result.estimated_tax() == R("325.00")


def test_estimated_tax_ignores_losses_rather_than_generating_a_refund():
    """A loss carries forward against future gains; it is not a negative
    liability on this transaction."""
    h = _h([_lot("L1", 10, "100.00", D(2026, 1, 1))])
    result = tax.sell_fifo(h, quantity=10, price=R("90.00"), on=D(2026, 6, 1))
    assert result.gain < 0
    assert result.estimated_tax() == money.ZERO


def test_a_long_term_loss_does_not_offset_a_short_term_gain_inside_one_sale():
    """The two buckets are taxed separately; netting them here would understate
    the STCG liability."""
    h = _h([
        _lot("OLD", 10, "200.00", D(2025, 1, 1)),     # sold at 150 -> LT loss
        _lot("NEW", 10, "100.00", D(2026, 9, 1)),     # sold at 150 -> ST gain
    ])
    result = tax.sell_fifo(h, quantity=20, price=R("150.00"), on=D(2026, 10, 1))

    assert result.long_term_gain == R("-500.00")
    assert result.short_term_gain == R("500.00")
    assert result.estimated_tax() == R("100.00")      # 20% of the ST gain only


def test_rates_match_the_project_wide_constants():
    assert tax.STCG_RATE == Decimal("0.20")
    assert tax.LTCG_RATE == Decimal("0.125")
