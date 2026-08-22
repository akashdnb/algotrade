"""Tests for drawdown-shape statistics."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.backtest import drawdown as dd


def _series(values: list[float]):
    days = pl.Series([date(2020, 1, 1) + timedelta(days=i) for i in range(len(values))])
    return pl.Series(values, dtype=pl.Float64), days


def test_a_single_recovered_episode_is_bounded_correctly():
    cap, days = _series([100, 90, 80, 90, 100, 110])
    (e,) = dd.episodes(cap, days)
    assert e.depth == pytest.approx(-0.20)
    assert (e.start, e.trough, e.recovery) == (days[1], days[2], days[4])
    assert e.recovered
    assert e.to_trough == 2      # sessions 1..2
    assert e.to_recover == 2     # trough -> recovery
    assert e.duration == 4       # start -> recovery inclusive


def test_an_unrecovered_episode_is_never_silently_closed():
    """Closing an open episode at the last session reports a recovery that did
    not happen, and shortens the longest-recovery figure exactly when the book
    is in its worst state."""
    cap, days = _series([100, 90, 70, 75, 80])
    (e,) = dd.episodes(cap, days)
    assert not e.recovered
    assert e.recovery is None
    assert e.to_recover is None
    assert e.depth == pytest.approx(-0.30)


def test_separate_peaks_produce_separate_episodes():
    cap, days = _series([100, 80, 100, 120, 96, 120, 130])
    eps = dd.episodes(cap, days)
    assert len(eps) == 2
    assert [round(e.depth, 4) for e in eps] == [-0.20, -0.20]
    assert all(e.recovered for e in eps)


def test_a_monotonically_rising_series_has_no_episodes():
    cap, days = _series([100, 101, 102, 103])
    assert dd.episodes(cap, days) == []
    assert dd.underwater_fraction(cap) == 0.0
    assert dd.ulcer_index(cap) == 0.0


def test_threshold_filters_shallow_dips():
    cap, days = _series([100, 99, 100, 60, 100])
    assert len(dd.episodes(cap, days)) == 2
    assert len(dd.episodes(cap, days, threshold=0.10)) == 1


def test_count_deeper_than_is_strict():
    cap, days = _series([100, 90, 100, 70, 100, 50, 100])
    eps = dd.episodes(cap, days)
    counts = dd.count_deeper_than(eps, 0.10, 0.20, 0.40, 0.60)
    assert counts == {0.10: 2, 0.20: 2, 0.40: 1, 0.60: 0}


def test_underwater_fraction_counts_strictly_below_peak():
    cap, _ = _series([100, 90, 80, 100, 100])
    # sessions 1 and 2 only; the recovery session is AT the peak, not below it.
    assert dd.underwater_fraction(cap) == pytest.approx(2 / 5)


def test_ulcer_index_charges_for_duration_where_max_drawdown_does_not():
    """The distinction max drawdown cannot make: a deep-but-brief fall against a
    shallow-but-long one."""
    brief, _ = _series([100, 70, 100, 100, 100, 100, 100, 100])
    long_, _ = _series([100, 80, 80, 80, 80, 80, 80, 100])
    from algotrade.backtest.metrics import max_drawdown

    assert max_drawdown(brief) < max_drawdown(long_)      # brief is deeper
    assert dd.ulcer_index(brief) < dd.ulcer_index(long_)  # but hurts less


def test_downside_capture_is_relative_and_directional():
    bench = pl.Series([-0.10, 0.10, -0.10, 0.10], dtype=pl.Float64)
    half = pl.Series([-0.05, 0.05, -0.05, 0.05], dtype=pl.Float64)
    double = pl.Series([-0.20, 0.20, -0.20, 0.20], dtype=pl.Float64)

    assert dd.downside_capture(half, bench) < 1.0     # defensive
    assert dd.downside_capture(bench, bench) == pytest.approx(1.0)
    assert dd.downside_capture(double, bench) > 1.0   # amplifies
    assert dd.upside_capture(half, bench) < 1.0


def test_downside_capture_on_a_benchmark_that_never_falls_is_nan():
    """Not 0.0 -- an undefined ratio must not read as 'perfectly defensive'."""
    import math

    bench = pl.Series([0.01, 0.02], dtype=pl.Float64)
    assert math.isnan(dd.downside_capture(bench, bench))


def test_capture_rejects_misaligned_series():
    a = pl.Series([0.1, 0.2], dtype=pl.Float64)
    b = pl.Series([0.1], dtype=pl.Float64)
    with pytest.raises(ValueError, match="aligned"):
        dd.downside_capture(a, b)


def test_martin_ratio_is_return_per_unit_of_ulcer():
    cap, _ = _series([100, 90, 100, 90, 100])
    ui = dd.ulcer_index(cap)
    assert dd.martin_ratio(0.20, cap) == pytest.approx(0.20 / (ui / 100))
    assert dd.martin_ratio(0.20, pl.Series([], dtype=pl.Float64)) == 0.0


def test_benchmark_move_uses_drawdown_not_point_to_point():
    """A round trip is not harmless: point-to-point would score this 0%."""
    days = [date(2020, 1, 1) + timedelta(days=i) for i in range(5)]
    bench = pl.DataFrame({"date": days, "capital": [100.0, 70.0, 80.0, 90.0, 100.0]})
    assert dd.benchmark_move(bench, days[0], days[4]) == pytest.approx(-0.30)


def test_capture_does_not_degenerate_over_many_down_days():
    """Regression: the compounded-across-down-days form sent both cumulatives to
    nearly -100%, so the ratio collapsed to 1.00 and reported a beta-0.71 book
    and a beta-1.07 book as identically market-like."""
    n = 1200
    bench = pl.Series([-0.01 if i % 2 else 0.011 for i in range(n)], dtype=pl.Float64)
    defensive = bench * 0.5
    aggressive = bench * 1.5

    assert dd.downside_capture(defensive, bench) == pytest.approx(0.5)
    assert dd.downside_capture(aggressive, bench) == pytest.approx(1.5)

    # The form that failed, shown failing: a book taking HALF the market's
    # downside scores ~0.95 instead of 0.50, because both cumulatives are
    # pinned near -100%. On the real series, where down days are larger, both
    # rounded to 1.00 exactly.
    mask = bench < 0
    compounded = (
        float((1 + defensive.filter(mask)).product() - 1)
        / float((1 + bench.filter(mask)).product() - 1)
    )
    assert compounded > 0.90
    assert abs(compounded - 0.5) > 0.4


def test_capture_by_period_compounds_within_but_not_across_periods():
    days = pl.Series([date(2020, 1, 1) + timedelta(days=i) for i in range(120)])
    bench = pl.Series([-0.01 if i % 2 else 0.005 for i in range(120)], dtype=pl.Float64)
    half = bench * 0.5
    got = dd.capture_by_period(days, half, bench, every="1mo")
    assert 0.4 < got < 0.6
