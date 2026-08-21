"""Tests for the transaction cost model (PROJECT.md §3).

The headline cases are golden tests against the worked examples in §3, computed
by hand from Zerodha's published rate card. Getting these wrong silently skews
every backtest result, so they are pinned to the paisa.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from algotrade.core import costs

CONFIG = Path(__file__).parent.parent / "config" / "costs.yaml"


@pytest.fixture(scope="module")
def cards() -> dict[str, costs.RateCard]:
    return costs.load_rate_cards(CONFIG)


@pytest.fixture(scope="module")
def slip() -> costs.SlippageModel:
    return costs.load_slippage(CONFIG)


# --- golden tests against the hand-computed §3 examples ----------------------


def test_delivery_round_trip_on_20k(cards):
    """§3 worked example: Rs 59.84 on a Rs 20,000 position, ~30 bps.

        brokerage  0.00   (zero on delivery)
        STT       40.00   (0.1% on BOTH sides -- the dominant cost)
        txn        1.23
        SEBI       0.04
        stamp      3.00   (buy side only)
        GST        0.23   (18% of brokerage + SEBI + txn only)
        DP        15.34   (flat, sell side)
    """
    c = costs.round_trip_charges(20_000, cards["delivery"])
    assert c.brokerage == pytest.approx(0.0)
    assert c.stt == pytest.approx(40.0)
    assert c.txn == pytest.approx(1.228)
    assert c.sebi == pytest.approx(0.04)
    assert c.stamp == pytest.approx(3.0)
    assert c.gst == pytest.approx(0.228, abs=0.001)
    assert c.dp == pytest.approx(15.34)
    assert c.total == pytest.approx(59.84, abs=0.01)
    assert c.bps_of(20_000) == pytest.approx(29.9, abs=0.1)


def test_intraday_round_trip_on_20k(cards):
    """§3 worked example: Rs 21.26, ~10.6 bps -- 2.8x cheaper than delivery."""
    c = costs.round_trip_charges(20_000, cards["intraday"])
    assert c.brokerage == pytest.approx(12.0)  # 0.03% = Rs 6, under the Rs 20 cap
    assert c.stt == pytest.approx(5.0)  # sell side only
    assert c.stamp == pytest.approx(0.6)
    assert c.dp == pytest.approx(0.0)  # no demat movement
    assert c.total == pytest.approx(21.26, abs=0.01)
    assert c.bps_of(20_000) == pytest.approx(10.6, abs=0.1)


def test_delivery_costs_about_2_8x_intraday(cards):
    """The reason §2's swing-vs-intraday cost argument had to be corrected."""
    d = costs.round_trip_charges(20_000, cards["delivery"]).total
    i = costs.round_trip_charges(20_000, cards["intraday"]).total
    assert d / i == pytest.approx(2.8, abs=0.1)


# --- structure of the charges ------------------------------------------------


def test_gst_excludes_stt_and_stamp(cards):
    """GST applies to brokerage, SEBI and exchange charges only.

    STT and stamp duty are taxes in their own right, not services.
    """
    card = cards["intraday"]
    c = costs.order_charges(100_000, "buy", card)
    assert c.gst == pytest.approx((c.brokerage + c.sebi + c.txn) * card.gst_pct)


def test_stt_is_charged_both_sides_on_delivery(cards):
    buy = costs.order_charges(50_000, "buy", cards["delivery"])
    sell = costs.order_charges(50_000, "sell", cards["delivery"])
    assert buy.stt == pytest.approx(50.0)
    assert sell.stt == pytest.approx(50.0)


def test_stt_is_sell_side_only_on_intraday(cards):
    assert costs.order_charges(50_000, "buy", cards["intraday"]).stt == 0.0
    assert costs.order_charges(50_000, "sell", cards["intraday"]).stt > 0


def test_stamp_duty_is_buy_side_only(cards):
    assert costs.order_charges(50_000, "sell", cards["delivery"]).stamp == 0.0
    assert costs.order_charges(50_000, "buy", cards["delivery"]).stamp > 0


def test_dp_is_sell_side_only_and_flat(cards):
    """Flat per scrip regardless of quantity -- so it cannot be a bps figure."""
    small = costs.order_charges(10_000, "sell", cards["delivery"])
    large = costs.order_charges(1_000_000, "sell", cards["delivery"])
    assert small.dp == large.dp == pytest.approx(15.34)
    assert costs.order_charges(10_000, "buy", cards["delivery"]).dp == 0.0


def test_brokerage_cap_makes_cost_nonlinear(cards):
    """min(pct, cap) means cost is not a single bps number."""
    card = cards["intraday"]
    small = costs.order_charges(10_000, "buy", card)
    large = costs.order_charges(10_000_000, "buy", card)
    assert small.brokerage == pytest.approx(3.0)  # 0.03%
    assert large.brokerage == pytest.approx(20.0)  # capped
    assert large.bps_of(10_000_000) < small.bps_of(10_000)


def test_zero_value_order_is_free(cards):
    assert costs.order_charges(0, "buy", cards["delivery"]).total == 0.0


# --- the DP position-sizing constraint --------------------------------------


@pytest.mark.parametrize(
    ("value", "expected_bps"),
    [
        (10_000, 15.3),
        (20_000, 7.7),
        (50_000, 3.1),
        (100_000, 1.5),
    ],
)
def test_dp_bps_scales_inversely_with_position_size(cards, value, expected_bps):
    """§3's table: this is what caps position count at Rs 2L."""
    assert costs.dp_bps(value, cards["delivery"]) == pytest.approx(
        expected_bps, abs=0.1
    )


def test_fewer_larger_positions_beat_more_smaller_ones(cards):
    """Rs 2L across 20 vs 8 positions: DP alone costs 9 bps more per rotation.

    The marginal diversification from 8 -> 20 names does not justify that.
    """
    card = cards["delivery"]
    twenty = costs.dp_bps(200_000 / 20, card)
    eight = costs.dp_bps(200_000 / 8, card)
    assert twenty == pytest.approx(15.3, abs=0.1)
    assert eight == pytest.approx(6.1, abs=0.1)
    assert twenty - eight == pytest.approx(9.2, abs=0.2)


# --- breakeven ---------------------------------------------------------------


def test_breakeven_includes_both_slippage_legs(cards):
    bare = costs.breakeven_bps(20_000, cards["delivery"])
    with_slip = costs.breakeven_bps(20_000, cards["delivery"], slippage_one_way_bps=5.0)
    assert bare == pytest.approx(29.9, abs=0.1)
    assert with_slip == pytest.approx(bare + 10.0, abs=0.01)


# --- slippage ----------------------------------------------------------------


def test_slippage_floor_is_half_the_spread(slip):
    """A vanishingly small order still crosses the spread."""
    got = costs.slippage_bps(1.0, 1e12, 0.02, "liquid", slip)
    assert got == pytest.approx(slip.spread_bps_by_tier["liquid"] / 2, abs=0.01)


def test_slippage_rises_with_participation(slip):
    small = costs.slippage_bps(10_000, 1e8, 0.02, "liquid", slip)
    large = costs.slippage_bps(5_000_000, 1e8, 0.02, "liquid", slip)
    assert large > small


def test_slippage_follows_the_square_root_law(slip):
    """4x the participation should roughly double the impact component."""
    half = slip.spread_bps_by_tier["liquid"] / 2
    a = costs.slippage_bps(1_000_000, 1e8, 0.02, "liquid", slip) - half
    b = costs.slippage_bps(4_000_000, 1e8, 0.02, "liquid", slip) - half
    assert b / a == pytest.approx(2.0, rel=0.01)


def test_illiquid_tiers_cost_more(slip):
    args = (100_000, 1e8, 0.02)
    liquid = costs.slippage_bps(*args, "liquid", slip)
    small = costs.slippage_bps(*args, "small", slip)
    assert small > liquid * 3


def test_unknown_tier_falls_back_to_the_worst(slip):
    args = (100_000, 1e8, 0.02)
    assert costs.slippage_bps(*args, "nonsense", slip) == costs.slippage_bps(
        *args, "small", slip
    )


def test_zero_turnover_returns_the_spread_floor(slip):
    got = costs.slippage_bps(100_000, 0.0, 0.02, "mid", slip)
    assert got == pytest.approx(slip.spread_bps_by_tier["mid"] / 2)


def test_participation_cap_bounds_order_size(slip):
    """Backtests ignoring this fill orders no real participant could."""
    assert costs.max_tradeable_value(1e8, slip) == pytest.approx(
        1e8 * slip.max_participation
    )
    assert costs.max_tradeable_value(-5, slip) == 0.0


# --- config ------------------------------------------------------------------


def test_rate_card_is_versioned():
    """Costs are data, not constants -- and the version must be traceable."""
    import yaml

    raw = yaml.safe_load(CONFIG.read_text())
    assert raw["version"].startswith("zerodha-")
    assert set(costs.load_rate_cards(CONFIG)) == {"delivery", "intraday"}
