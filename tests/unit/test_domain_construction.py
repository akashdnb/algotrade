"""Tests for turning fractional target weights into integer share counts.

This is where the backtest's clean arithmetic meets reality: you cannot buy
0.6 of a share, and at ₹2L a 1/30 position is ~₹6,700 against share prices
that range from ₹50 to ₹15,000. The rounding error is material and must be
reported rather than hidden.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.domain import construction, money

R = money.rupees


def test_exact_division_needs_no_rounding():
    book = construction.build(
        targets={"A": 0.5, "B": 0.5},
        investable=R("2000.00"),
        prices={"A": R("100.00"), "B": R("100.00")},
    )
    assert book.quantities == {"A": 10, "B": 10}
    assert book.deployed == R("2000.00")
    assert book.residual_cash == money.ZERO


def test_fractional_shares_are_floored_then_topped_up_by_largest_remainder():
    """Flooring alone leaves cash idle. The leftover buys one more share of
    whichever name was rounded down hardest."""
    book = construction.build(
        targets={"A": 0.5, "B": 0.5},
        investable=R("1000.00"),
        prices={"A": R("300.00"), "B": R("400.00")},
    )
    # ideal: A 500/300 = 1.67 -> 1 (rem .67), B 500/400 = 1.25 -> 1 (rem .25)
    # floor cost 700, leftover 300 -> A has the larger remainder and fits.
    assert book.quantities == {"A": 2, "B": 1}
    assert book.deployed == R("1000.00")
    assert book.residual_cash == money.ZERO


def test_topping_up_never_exceeds_available_cash():
    book = construction.build(
        targets={"A": 0.5, "B": 0.5},
        investable=R("1000.00"),
        prices={"A": R("900.00"), "B": R("400.00")},
    )
    # A: 500/900 -> 0 shares. B: 500/400 -> 1 share (400). Leftover 600.
    # A's remainder is larger but A costs 900 > 600, so B gets the top-up.
    assert book.deployed <= R("1000.00")
    assert book.quantities["B"] >= 1


def test_a_position_below_the_minimum_trade_value_is_dropped_with_a_reason():
    """Real brokers have minimums and the flat ₹15.34 DP charge makes a ₹300
    trade cost 500+ bps. Not trading is strictly better."""
    book = construction.build(
        targets={"A": 0.99, "B": 0.01},
        investable=R("20000.00"),
        prices={"A": R("100.00"), "B": R("50.00")},
        min_trade_value=R("500.00"),
    )
    assert "B" not in book.quantities
    assert "B" in book.dropped
    assert "minimum" in book.dropped["B"].lower()


def test_a_share_priced_above_the_whole_target_is_dropped_not_silently_zeroed():
    book = construction.build(
        targets={"A": 0.5, "B": 0.5},
        investable=R("10000.00"),
        prices={"A": R("100.00"), "B": R("15000.00")},
    )
    assert "B" in book.dropped
    assert book.quantities.get("B") is None


def test_a_missing_price_is_dropped_and_reported_never_guessed():
    book = construction.build(
        targets={"A": 0.5, "B": 0.5},
        investable=R("10000.00"),
        prices={"A": R("100.00")},
    )
    assert "B" in book.dropped
    assert "price" in book.dropped["B"].lower()


def test_residual_cash_is_what_actually_remains():
    book = construction.build(
        targets={"A": 1.0},
        investable=R("1000.00"),
        prices={"A": R("300.00")},
    )
    assert book.quantities == {"A": 3}
    assert book.deployed == R("900.00")
    assert book.residual_cash == R("100.00")


def test_rounding_drag_reports_how_far_the_book_is_from_its_target():
    """At ₹2L with 30 names this is a real number, and a book that silently
    deploys 94% of capital is not the strategy that was validated."""
    book = construction.build(
        targets={"A": 1.0},
        investable=R("1000.00"),
        prices={"A": R("300.00")},
    )
    assert book.rounding_drag == pytest.approx(0.10)   # 100 of 1000 undeployed


def test_zero_investable_produces_an_empty_book_rather_than_an_error():
    book = construction.build(
        targets={"A": 1.0}, investable=money.ZERO, prices={"A": R("100.00")}
    )
    assert book.quantities == {}
    assert book.residual_cash == money.ZERO


def test_reserve_for_charges_holds_back_a_proportional_buffer():
    """Without a reserve the last buy fails for want of a few rupees, which is
    a silent partial execution rather than a clean plan."""
    assert construction.reserve_for_charges(R("100000.00"), bps=30) == R("300.00")


def test_build_applied_after_a_reserve_leaves_the_reserve_untouched():
    investable = R("10000.00") - construction.reserve_for_charges(R("10000.00"), bps=30)
    book = construction.build(
        targets={"A": 1.0}, investable=investable, prices={"A": R("100.00")}
    )
    assert book.deployed <= investable
    assert book.quantities == {"A": 99}          # 9,970 / 100, floored


def test_weights_are_used_as_given_and_not_renormalised():
    """A 90%-invested target is a deliberate instruction (cash drag by design);
    silently scaling it to 100% would override the caller's intent."""
    book = construction.build(
        targets={"A": 0.45, "B": 0.45},
        investable=R("10000.00"),
        prices={"A": R("100.00"), "B": R("100.00")},
    )
    assert book.quantities == {"A": 45, "B": 45}
    assert book.residual_cash == R("1000.00")
