from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from algotrade.zones import study


def _bars(rows, start=date(2024, 1, 1), eligible=True):
    """rows: (open, high, low, close) per consecutive session."""
    panel = pl.DataFrame([
        {"symbol": "ABC", "date": start + timedelta(days=i), "open": o, "high": h, "low": l, "close": c, "eligible": eligible}
        for i, (o, h, l, c) in enumerate(rows)
    ])
    return study.build_bars(panel)["ABC"], panel


def _day(i, start=date(2024, 1, 1)):
    return study.epoch_day(start + timedelta(days=i))


def test_entry_is_next_open_after_first_reach():
    bars, _ = _bars([(20, 21, 19, 20), (20, 21, 15, 16), (16, 17, 15, 16.5), (16.5, 18, 16, 17)])
    found = study.find_entry(bars, demand=True, low=14.0, high=16.0, after_day=_day(0))
    assert (found.reach, found.entry, found.age, found.status) == (1, 2, 1, "ok")
    assert bars.open[found.entry] == 16


def test_close_through_far_edge_on_touch_is_not_a_trade():
    bars, _ = _bars([(20, 21, 19, 20), (20, 21, 12, 13), (13, 14, 12, 13)])
    found = study.find_entry(bars, demand=True, low=14.0, high=16.0, after_day=_day(0))
    assert found.status == "broken_on_touch" and found.entry is None


def test_gap_open_beyond_far_edge_is_not_a_trade():
    bars, _ = _bars([(20, 21, 19, 20), (20, 21, 15, 15.5), (13, 14, 12, 13)])
    found = study.find_entry(bars, demand=True, low=14.0, high=16.0, after_day=_day(0))
    assert found.status == "gap_through" and found.entry is None


def test_supply_zone_reached_from_below():
    bars, _ = _bars([(10, 11, 9, 10), (10, 13, 10, 12.5), (12.5, 13, 12, 12.4)])
    found = study.find_entry(bars, demand=False, low=12.0, high=14.0, after_day=_day(0))
    assert found.status == "ok" and found.reach == 1 and found.entry == 2


def test_never_reached_returns_none_and_search_starts_after_creation():
    bars, _ = _bars([(20, 21, 15, 16), (20, 21, 19, 20), (20, 21, 19, 20)])
    # Session 0 touches the zone but happened on/before the creation day.
    assert study.find_entry(bars, demand=True, low=14.0, high=16.0, after_day=_day(0)) is None


def test_outcomes_are_signed_and_stop_aware():
    rows = [(20, 21, 19, 20), (20, 21, 15, 16), (16, 17, 16, 16.5), (16.5, 17, 12, 13), (13, 15.5, 12, 15)]
    bars, panel = _bars(rows)
    market = study.build_market(panel)
    out = study.outcomes(bars, 2, demand=True, stop=14.0, market=market, horizons=(3,))
    # Held to the close of session 4 (15) vs stopped out on session 3 at 14.
    assert out["ret_3"] == pytest.approx(15 / 16 - 1)
    assert out["ret_stop_3"] == pytest.approx(14 / 16 - 1)
    short = study.outcomes(bars, 2, demand=False, stop=17.5, market=market, horizons=(3,))
    assert short["ret_3"] == pytest.approx(-(15 / 16 - 1))


def test_horizon_beyond_data_is_none():
    bars, panel = _bars([(20, 21, 19, 20), (20, 21, 15, 16), (16, 17, 16, 16.5)])
    out = study.outcomes(bars, 2, demand=True, stop=14.0, market=study.build_market(panel), horizons=(5,))
    assert out["ret_5"] is None and out["abn_5"] is None


def test_market_window_return_spans_open_to_close():
    rows = [(10, 10, 10, 10), (10, 11, 10, 11), (11, 12, 11, 12)]
    _, panel = _bars(rows)
    market = study.build_market(panel)
    # Open of day 1 (10) to close of day 2 (12) for a single-name market.
    assert market.window_return(_day(1), _day(2)) == pytest.approx(12 / 10 - 1)


def test_ineligible_reach_session_is_dropped():
    bars, panel = _bars([(20, 21, 19, 20), (20, 21, 15, 16), (16, 17, 15, 16.5), (16.5, 18, 16, 17)], eligible=False)
    found = study.find_entry(bars, demand=True, low=14.0, high=16.0, after_day=_day(0))
    assert study.event_row(bars, found, demand=True, low=14.0, high=16.0, market=study.build_market(panel.with_columns(pl.lit(True).alias("eligible")))) is None


def test_controls_are_deterministic_and_reuse_zone_geometry():
    rng = np.random.default_rng(3)
    close = 100 * np.cumprod(1 + rng.normal(0, 0.02, 300))
    rows = [(c, c * 1.01, c * 0.99, c) for c in close]
    bars, panel = _bars(rows)
    market = study.build_market(panel)
    kwargs = dict(demand=True, off_low=-0.08, off_high=-0.03, created_day=_day(50), k=5, seed_key="z", market=market)
    first = study.matched_controls(bars, **kwargs)
    assert first == study.matched_controls(bars, **kwargs)
    assert first and all(r["risk_pct"] > 0 for r in first)


def test_collapse_merges_nested_zones_into_one_trade():
    rows = [
        {"symbol": "A", "entry_day": 1, "zone_type": "demand", "abn_5": 0.02},
        {"symbol": "A", "entry_day": 1, "zone_type": "demand", "abn_5": 0.04},
        {"symbol": "A", "entry_day": 2, "zone_type": "demand", "abn_5": 0.01},
    ]
    merged = study.collapse_events(rows, ["abn_5"])
    assert len(merged) == 2
    assert next(r for r in merged if r["entry_day"] == 1)["abn_5"] == pytest.approx(0.03)


def test_paired_difference_detects_a_real_shift_and_is_silent_on_noise():
    rng = np.random.default_rng(0)
    base = date(2015, 1, 1).toordinal() - date(1970, 1, 1).toordinal()

    def make(shift):
        events = []
        for i in range(600):
            events.append({"entry_day": base + (i % 120) * 25, "abn_5": float(rng.normal(shift, 0.05)),
                           "controls": [{"abn_5": float(rng.normal(0, 0.05))} for _ in range(3)]})
        return events

    shifted = study.paired_difference(make(0.03), "abn_5")
    assert shifted["lo"] > 0
    null = study.paired_difference(make(0.0), "abn_5")
    assert null["lo"] < 0 < null["hi"]


def test_collapse_pools_controls_of_nested_zones():
    rows = [
        {"symbol": "A", "entry_day": 1, "zone_type": "demand", "abn_5": 0.02, "controls": [{"abn_5": 0.0}]},
        {"symbol": "A", "entry_day": 1, "zone_type": "demand", "abn_5": 0.04, "controls": [{"abn_5": 0.1}]},
    ]
    (merged,) = study.collapse_events(rows, ["abn_5"])
    assert len(merged["controls"]) == 2


def test_trend_flag_is_causal_and_false_before_window():
    close = np.concatenate([np.full(5, 10.0), np.linspace(10, 20, 10), np.linspace(20, 5, 10)])
    flag = study._trend_up(close, window=5)
    assert not flag[:4].any()
    assert flag[10] and not flag[-1]
    # Changing future closes never changes an earlier flag.
    other = close.copy()
    other[-3:] = 100.0
    assert (study._trend_up(other, window=5)[:-3] == flag[:-3]).all()


def test_broken_day_uses_closes_and_has_confluence_respects_knowledge():
    bars, _ = _bars([(20, 21, 19, 20), (20, 21, 15, 16), (16, 17, 12, 13), (13, 14, 12, 13)])
    assert study.broken_day(bars, demand=True, low=14.0, high=16.0, after_day=_day(0)) == _day(2)
    assert study.broken_day(bars, demand=True, low=5.0, high=6.0, after_day=_day(0)) == 10**9
    others = [(10.0, 15.0, 100, 200)]
    assert study.has_confluence(others, low=14.0, high=18.0, reach_day=150)
    assert not study.has_confluence(others, low=14.0, high=18.0, reach_day=100)  # formed the same day: unknown
    assert not study.has_confluence(others, low=14.0, high=18.0, reach_day=201)  # already broken
    assert not study.has_confluence(others, low=16.0, high=18.0, reach_day=150)  # no overlap
