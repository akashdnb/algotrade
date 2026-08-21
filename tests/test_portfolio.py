"""Tests for portfolio construction (PROJECT.md §5)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.backtest import lookahead
from algotrade.core import portfolio as pf

D = date


def weights(rows: list[tuple[date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64},
        orient="row",
    )


def returns(values: list[float], start: date = D(2024, 1, 1)) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [start + timedelta(days=i) for i in range(len(values))],
            "net_return": values,
        }
    )


# --- position caps -----------------------------------------------------------


def test_cap_limits_single_name_weight():
    w = weights([(D(2024, 1, 1), "BIG", 0.9), (D(2024, 1, 1), "SMALL", 0.1)])
    out = pf.cap_weights(w, max_weight=0.2)
    assert out["weight"].abs().max() <= 0.2 + 1e-9


def test_cap_preserves_gross_when_there_is_headroom():
    """The excess is pushed onto names below the cap, not scaled back in."""
    w = weights([(D(2024, 1, 1), "A", 0.9), (D(2024, 1, 1), "B", 0.1)])
    before = w["weight"].abs().sum()
    out = pf.cap_weights(w, max_weight=0.5)
    assert out["weight"].abs().sum() == pytest.approx(before)
    assert out["weight"].abs().max() <= 0.5 + 1e-9


def test_cap_lowers_gross_when_too_tight_to_absorb():
    """Two names capped at 0.2 cannot carry gross 1.0. Reporting the lower
    gross is honest; silently exceeding the cap would not be."""
    w = weights([(D(2024, 1, 1), "A", 0.9), (D(2024, 1, 1), "B", 0.1)])
    out = pf.cap_weights(w, max_weight=0.2)
    assert out["weight"].abs().max() <= 0.2 + 1e-9
    assert out["weight"].abs().sum() == pytest.approx(0.4)


def test_cap_handles_shorts():
    w = weights([(D(2024, 1, 1), "A", -0.9), (D(2024, 1, 1), "B", 0.1)])
    out = pf.cap_weights(w, max_weight=0.3)
    assert out["weight"].min() >= -0.3 - 1e-9


def test_cap_on_empty():
    assert pf.cap_weights(weights([])).is_empty()


# --- turnover control: the decisive lever -----------------------------------


def test_resample_holds_weights_between_rebalances():
    """Weekly cadence must repeat the last rebalance's weights."""
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(14)]
    # Weights change every single day.
    w = weights([(d, "A", 0.1 * (i + 1)) for i, d in enumerate(days)])
    out = pf.resample(w, "weekly")
    distinct = out["weight"].unique().len()
    assert distinct < len(days)  # far fewer distinct targets
    assert out["date"].n_unique() == len(days)  # but still a position every day


def test_resample_cuts_the_number_of_target_changes():
    """The mechanism behind the -21% to break-even swing in section 11a."""
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    w = weights([(d, "A", 0.5 if i % 2 else -0.5) for i, d in enumerate(days)])

    def changes(frame: pl.DataFrame) -> int:
        s = frame.sort("date")["weight"].to_list()
        return sum(1 for a, b in zip(s, s[1:]) if a != b)

    assert changes(pf.resample(w, "monthly")) < changes(pf.resample(w, "weekly"))
    assert changes(pf.resample(w, "weekly")) < changes(w)


def test_resample_daily_is_a_noop():
    w = weights([(D(2024, 1, 1), "A", 0.5), (D(2024, 1, 2), "A", 0.6)])
    assert pf.resample(w, "daily").equals(w)


def test_no_trade_band_ignores_small_drift():
    days = [D(2024, 1, 1), D(2024, 1, 2)]
    w = weights([(days[0], "A", 0.20), (days[1], "A", 0.205)])
    out = pf.apply_no_trade_band(w, band=0.02)
    assert out.filter(pl.col("date") == days[1])["weight"][0] == pytest.approx(0.20)


def test_no_trade_band_allows_large_moves():
    days = [D(2024, 1, 1), D(2024, 1, 2)]
    w = weights([(days[0], "A", 0.20), (days[1], "A", 0.40)])
    out = pf.apply_no_trade_band(w, band=0.02)
    assert out.filter(pl.col("date") == days[1])["weight"][0] == pytest.approx(0.40)


def test_no_trade_band_always_permits_a_full_exit():
    """Damping an exit would strand a position the strategy wants gone."""
    days = [D(2024, 1, 1), D(2024, 1, 2)]
    w = weights([(days[0], "A", 0.005), (days[1], "B", 0.005)])
    out = pf.apply_no_trade_band(w, band=0.02)
    assert "A" not in out.filter(pl.col("date") == days[1])["symbol"].to_list()


# --- vol targeting -----------------------------------------------------------


def test_leverage_is_inverse_to_realised_vol():
    # Both series need genuine variation: zero-variance returns give an
    # undefined leverage, which vol_scalar reports as null on purpose.
    calm = pf.vol_scalar(returns([0.002, -0.001] * 50 + [0.001]), target=0.08)
    wild = pf.vol_scalar(returns([0.05, -0.05] * 50 + [0.01]), target=0.08)
    assert calm["leverage"][-1] > wild["leverage"][-1]


def test_zero_variance_returns_null_leverage():
    """A flat return series implies infinite leverage, so it is refused."""
    out = pf.vol_scalar(returns([0.001] * 100), target=0.08)
    assert out["leverage"][-1] is None


def test_leverage_is_capped():
    """A near-zero-vol history must not imply unbounded leverage."""
    out = pf.vol_scalar(returns([0.0001] * 100), target=0.08, max_leverage=2.5)
    assert (out["leverage"].drop_nulls() <= 2.5 + 1e-9).all()


def test_leverage_is_null_until_the_window_fills():
    out = pf.vol_scalar(returns([0.01] * 10), lookback=60)
    assert out["leverage"].null_count() == out.height


def test_vol_scalar_excludes_the_current_period():
    """Scaling by a vol that includes today's return is look-ahead.

    The bug is subtle because the equity curve still looks plausible.
    """
    base = [0.001] * 80
    calm = pf.vol_scalar(returns(base + [0.001]), lookback=20)
    spike = pf.vol_scalar(returns(base + [0.30]), lookback=20)
    # The final row's leverage must be identical: the spike is not yet visible.
    assert calm["leverage"][-1] == pytest.approx(spike["leverage"][-1])


def test_vol_scalar_has_no_lookahead():
    """Same check, run mechanically through the detector."""
    frame = returns([0.01, -0.02, 0.015, -0.01] * 15)

    def fn(p: pl.DataFrame) -> pl.DataFrame:
        return pf.vol_scalar(p, lookback=10)

    lookahead.assert_no_lookahead(
        frame, fn, columns=["leverage", "realised_vol"], keys=("date",)
    )


def test_vol_scalar_on_empty():
    out = pf.vol_scalar(returns([]))
    assert "leverage" in out.columns


# --- applying leverage -------------------------------------------------------


def test_scale_multiplies_weights():
    w = weights([(D(2024, 1, 1), "A", 0.4)])
    lev = pl.DataFrame({"date": [D(2024, 1, 1)], "leverage": [2.0]})
    assert pf.scale(w, lev)["weight"][0] == pytest.approx(0.8)


def test_scale_drops_dates_without_leverage():
    """Never mix levered and unlevered regimes in one equity curve."""
    w = weights([(D(2024, 1, 1), "A", 0.4), (D(2024, 1, 2), "A", 0.4)])
    lev = pl.DataFrame(
        {"date": [D(2024, 1, 1), D(2024, 1, 2)], "leverage": [None, 2.0]},
        schema={"date": pl.Date, "leverage": pl.Float64},
    )
    out = pf.scale(w, lev)
    assert out["date"].to_list() == [D(2024, 1, 2)]


# --- diagnostics -------------------------------------------------------------


def test_gross_exposure_reports_market_neutrality():
    w = weights([(D(2024, 1, 1), "A", 0.5), (D(2024, 1, 1), "B", -0.5)])
    row = pf.gross_exposure(w).row(0, named=True)
    assert row["gross"] == pytest.approx(1.0)
    assert row["net"] == pytest.approx(0.0)
    assert row["positions"] == 2
