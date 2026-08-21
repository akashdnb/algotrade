"""Tests for fold-based stability evaluation (PROJECT.md §8.1, §8.3)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.backtest import walkforward as wf

D = date


def sessions(n: int, start: date = D(2012, 1, 2)) -> list[date]:
    """n weekday sessions."""
    out, day = [], start
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def equity(rets: list[float], days: list[date] | None = None) -> pl.DataFrame:
    days = days or sessions(len(rets))
    caps, c = [], 100_000.0
    for r in rets:
        c *= 1 + r
        caps.append(c)
    return pl.DataFrame(
        {
            "date": days,
            "capital": caps,
            "gross_return": rets,
            "net_return": rets,
            "cost": [0.0] * len(rets),
            "turnover": [0.0] * len(rets),
        }
    )


# --- fold construction -------------------------------------------------------


def test_folds_are_contiguous_and_cover_the_span():
    days = sessions(3000)
    folds = wf.make_folds(days, n_folds=5, min_sessions=250)
    assert len(folds) == 5
    assert folds[0].purged_from == days[0]
    assert folds[-1].end == days[-1]
    for a, b in zip(folds, folds[1:]):
        assert a.end < b.purged_from


def test_purge_moves_the_start_without_moving_the_boundary():
    """The purged sessions are dropped from evaluation but still delimit the fold."""
    days = sessions(3000)
    folds = wf.make_folds(days, n_folds=3, purge=273, min_sessions=250)
    for f in folds:
        assert f.start > f.purged_from
        # Exactly `purge` sessions were skipped.
        idx_from = days.index(f.purged_from)
        assert days[idx_from + 273] == f.start


def test_short_folds_are_discarded():
    """A 60-session fold yields a Sharpe with a standard error near 2."""
    days = sessions(1200)
    folds = wf.make_folds(days, n_folds=4, min_sessions=250)
    assert all(
        (days.index(f.end) - days.index(f.start)) >= 250 for f in folds
    )


def test_impossible_split_raises_rather_than_returning_junk():
    with pytest.raises(ValueError, match="cannot support"):
        wf.make_folds(sessions(600), n_folds=5, purge=273, min_sessions=250)


def test_n_folds_must_be_positive():
    with pytest.raises(ValueError, match="n_folds"):
        wf.make_folds(sessions(1000), n_folds=0)


# --- the stability question this exists to answer ---------------------------


def test_one_good_regime_carrying_a_decade_is_detected():
    """§8.1's regime gate: specified, never implemented until now.

    A strategy that earns everything in its first fifth and bleeds thereafter has
    an attractive full-period CAGR and is not stable.
    """
    rets = [0.004] * 600 + [-0.0004] * 2400
    report = wf.evaluate(equity(rets), n_folds=5, min_sessions=250)
    assert report.positive_folds == 1
    assert not report.stable()


def test_a_consistently_positive_strategy_is_stable():
    rets = [0.0004, -0.0002] * 1500
    report = wf.evaluate(equity(rets), n_folds=5, min_sessions=250)
    assert report.positive_folds == 5
    assert report.stable()


def test_sharpe_spread_is_an_error_bar():
    """Fold dispersion is more honest than a point estimate with no interval."""
    steady = wf.evaluate(equity([0.0004, -0.0002] * 1500), n_folds=5)
    lumpy = wf.evaluate(equity([0.004] * 600 + [-0.0004] * 2400), n_folds=5)
    assert lumpy.sharpe_spread > steady.sharpe_spread


def test_risk_free_defaults_to_zero_for_a_neutral_book():
    """A dollar-neutral spread is already self-financing (see metrics.compute)."""
    rets = [0.0004, -0.0002] * 1500
    zero = wf.evaluate(equity(rets), n_folds=3)
    charged = wf.evaluate(equity(rets), n_folds=3, risk_free=0.065)
    assert zero.folds[0].sharpe > charged.folds[0].sharpe


# --- mechanics ---------------------------------------------------------------


def test_combined_excludes_purged_sessions():
    """The combined series is not the full-period series and must not be
    compared to it directly."""
    rets = [0.0004] * 3000
    report = wf.evaluate(equity(rets), n_folds=3, purge=100, min_sessions=250)
    assert report.combined.height < 3000
    assert report.combined.height == sum(f.periods for f in report.folds)


def test_summary_states_the_caveat():
    """The module must not let a reader mistake folds for out-of-sampleness."""
    report = wf.evaluate(equity([0.0004, -0.0002] * 1500), n_folds=3)
    text = report.summary()
    assert "not out-of-sample" in text
    assert "nothing was fitted" in text


def test_empty_equity_is_handled():
    report = wf.evaluate(pl.DataFrame())
    assert report.folds == []
    assert not report.stable()
