"""Tests for NAV, unit accounting, drawdown and XIRR.

Unit accounting is the reason this module exists. With ₹20k arriving monthly,
raw NAV growth conflates "the strategy worked" with "I deposited money", and
§11n turned on exactly that distinction: a contributor's real return is
money-weighted (XIRR), not time-weighted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain import money, valuation
from algotrade.domain.models import Holding, Lot

D = date
R = money.rupees


def _h(symbol: str, qty: int, price: str = "100.00") -> Holding:
    return Holding(portfolio_id="p", symbol=symbol,
                   lots=[Lot(lot_id=f"L-{symbol}", acquired_on=D(2026, 1, 1),
                             quantity=qty, price=R(price))])


# -------------------------------------------------------------------- NAV ---


def test_nav_is_positions_marked_to_close_plus_cash():
    positions, invested = valuation.value_positions(
        [_h("A", 10), _h("B", 5)],
        prices={"A": R("110.00"), "B": R("200.00")},
    )
    assert invested == R("2100.00")          # 1,100 + 1,000
    assert len(positions) == 2


def test_weights_are_computed_against_nav_including_cash():
    """Weighting against invested value alone hides cash drag: a book that is
    half cash would report 100% invested."""
    positions, invested = valuation.value_positions(
        [_h("A", 10)], prices={"A": R("100.00")}, nav=R("2000.00")
    )
    assert positions[0].weight == pytest.approx(0.5)


def test_a_position_with_no_price_is_carried_at_zero_and_reported():
    """A suspended name must not vanish from the snapshot -- disappearing
    silently would understate the book and hide the problem."""
    positions, invested = valuation.value_positions(
        [_h("A", 10), _h("GONE", 5)], prices={"A": R("100.00")}
    )
    by_symbol = {p.symbol: p for p in positions}
    assert by_symbol["GONE"].value == money.ZERO
    assert by_symbol["GONE"].quantity == 5
    assert invested == R("1000.00")


# ------------------------------------------------------------------ units ---


def test_the_first_contribution_sets_the_unit_nav_to_the_base():
    units, unit_nav = valuation.apply_contribution(
        units=money.ZERO, nav_before=money.ZERO, contribution=R("200000.00")
    )
    assert unit_nav == valuation.BASE_UNIT_NAV
    assert units == R("2000.000")            # 200,000 / 100


def test_a_contribution_does_not_move_the_unit_nav():
    """This is the whole point: depositing money must not look like a return."""
    units, unit_nav = valuation.apply_contribution(
        units=R("2000.000"), nav_before=R("220000.00"), contribution=R("20000.00")
    )
    # unit_nav before = 220,000 / 2,000 = 110
    assert unit_nav == R("110.00")
    # 20,000 / 110 = 181.818... new units
    assert units == pytest.approx(Decimal("2181.818"), abs=Decimal("0.001"))


def test_unit_nav_moves_only_with_performance():
    before = valuation.unit_nav(nav=R("220000.00"), units=R("2000.000"))
    after = valuation.unit_nav(nav=R("242000.00"), units=R("2000.000"))
    assert before == R("110.00")
    assert after == R("121.00")
    assert money.percent_change(after, before) == pytest.approx(0.10)


def test_unit_nav_of_a_portfolio_with_no_units_is_the_base_not_a_crash():
    assert valuation.unit_nav(nav=money.ZERO, units=money.ZERO) == valuation.BASE_UNIT_NAV


# --------------------------------------------------------------- drawdown ---


def test_drawdown_is_zero_at_a_new_peak():
    assert valuation.drawdown(unit_nav=R("120.00"), peak=R("110.00")) == 0.0


def test_drawdown_is_negative_below_the_peak():
    assert valuation.drawdown(unit_nav=R("99.00"), peak=R("110.00")) == pytest.approx(-0.10)


def test_the_peak_ratchets_and_never_falls():
    assert valuation.new_peak(unit_nav=R("120.00"), peak=R("110.00")) == R("120.00")
    assert valuation.new_peak(unit_nav=R("100.00"), peak=R("110.00")) == R("110.00")


def test_drawdown_against_a_zero_peak_is_zero():
    assert valuation.drawdown(unit_nav=R("100.00"), peak=money.ZERO) == 0.0


# ------------------------------------------------------------------- XIRR ---


def test_xirr_of_a_single_doubling_over_one_year_is_about_100_percent():
    rate = valuation.xirr([
        (D(2026, 1, 1), R("-100000.00")),
        (D(2027, 1, 1), R("200000.00")),
    ])
    assert rate == pytest.approx(1.0, abs=0.01)


def test_xirr_of_a_flat_investment_is_about_zero():
    rate = valuation.xirr([
        (D(2026, 1, 1), R("-100000.00")),
        (D(2027, 1, 1), R("100000.00")),
    ])
    assert rate == pytest.approx(0.0, abs=1e-4)


def test_xirr_handles_monthly_contributions():
    """The realistic shape: ₹2L up front then ₹20k monthly, ending above cost.
    Money-weighted differs from time-weighted here, which is the point."""
    flows = [(D(2026, 1, 1), R("-200000.00"))]
    for m in range(2, 13):
        flows.append((D(2026, m, 1), R("-20000.00")))
    flows.append((D(2027, 1, 1), R("460000.00")))     # ~₹420k in, ₹460k out

    rate = valuation.xirr(flows)
    assert 0.05 < rate < 0.25            # plausible band; exact value is data


def test_xirr_reports_a_loss_as_negative():
    rate = valuation.xirr([
        (D(2026, 1, 1), R("-100000.00")),
        (D(2027, 1, 1), R("90000.00")),
    ])
    assert rate == pytest.approx(-0.10, abs=0.01)


def test_xirr_returns_none_when_undefined_rather_than_a_misleading_number():
    assert valuation.xirr([]) is None
    assert valuation.xirr([(D(2026, 1, 1), R("-100.00"))]) is None
    # All-positive flows: no investment was ever made, so there is no return.
    assert valuation.xirr([(D(2026, 1, 1), R("100.00")),
                           (D(2027, 1, 1), R("200.00"))]) is None


def test_xirr_is_insensitive_to_flow_ordering():
    ordered = valuation.xirr([
        (D(2026, 1, 1), R("-100000.00")), (D(2027, 1, 1), R("200000.00"))])
    shuffled = valuation.xirr([
        (D(2027, 1, 1), R("200000.00")), (D(2026, 1, 1), R("-100000.00"))])
    assert ordered == pytest.approx(shuffled)


def test_twr_annualises_a_unit_nav_series_by_calendar_span():
    """Row count would annualise a 250-session year as a 250-day year. §11j.6
    found exactly that defect in vs_benchmark_series."""
    rate = valuation.twr(
        first_unit_nav=R("100.00"), last_unit_nav=R("121.00"),
        start=D(2026, 1, 1), end=D(2028, 1, 1),
    )
    assert rate == pytest.approx(0.10, abs=0.001)      # 21% over 2 years


def test_twr_of_a_sub_year_span_is_still_annualised():
    rate = valuation.twr(
        first_unit_nav=R("100.00"), last_unit_nav=R("110.00"),
        start=D(2026, 1, 1), end=D(2026, 7, 2),        # ~half a year
    )
    assert rate > 0.19                                  # ~21% annualised


def test_twr_is_none_for_a_zero_length_span():
    assert valuation.twr(first_unit_nav=R("100.00"), last_unit_nav=R("110.00"),
                         start=D(2026, 1, 1), end=D(2026, 1, 1)) is None
