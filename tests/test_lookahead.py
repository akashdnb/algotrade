"""Tests for the look-ahead detector (PROJECT.md §6, test #1).

Half of these deliberately introduce look-ahead and assert the detector catches
it. A detector that never fires is worthless, so proving it fires is the point.

The bugs used here are the realistic ones: none is visible by reading the code
carefully, and all of them leave results looking plausible.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.backtest import lookahead
from algotrade.core import universe as uv


def panel(n_days: int = 40, symbols: tuple[str, ...] = ("A", "B", "C")) -> pl.DataFrame:
    start = date(2024, 1, 1)
    rows = []
    for i in range(n_days):
        for j, sym in enumerate(symbols):
            # Deterministic but non-monotonic, so a shift actually changes values.
            close = 100.0 + (i * 7 + j * 13) % 23
            rows.append(
                (start + timedelta(days=i), sym, close, close, close, close,
                 1_000_000.0 + i)
            )
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "symbol": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "turnover": pl.Float64,
        },
        orient="row",
    )


# --- clean features must pass ------------------------------------------------


def trailing_return(p: pl.DataFrame) -> pl.DataFrame:
    """Backward-looking only. The correct shape."""
    return p.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("feat")
    )


def test_clean_feature_passes():
    report = lookahead.check(panel(), trailing_return, columns=["feat"])
    assert report.clean, report.summary()
    assert "clean" in report.summary()


def test_assert_helper_is_silent_when_clean():
    lookahead.assert_no_lookahead(panel(), trailing_return, columns=["feat"])


# --- each realistic bug must be caught --------------------------------------


def forward_shift(p: pl.DataFrame) -> pl.DataFrame:
    """Wrong sign on the shift: reads tomorrow's price. The classic."""
    return p.sort(["symbol", "date"]).with_columns(
        (pl.col("close").shift(-1).over("symbol") / pl.col("close") - 1).alias("feat")
    )


def centred_window(p: pl.DataFrame) -> pl.DataFrame:
    """A centred rolling mean -- half the window is in the future."""
    return p.sort(["symbol", "date"]).with_columns(
        pl.col("close")
        .rolling_mean(window_size=7, center=True)
        .over("symbol")
        .alias("feat")
    )


def full_sample_zscore(p: pl.DataFrame) -> pl.DataFrame:
    """Normalised by whole-history mean and std -- uses the future's statistics.

    Especially insidious: the feature looks well-behaved and the bias is subtle.
    """
    return p.with_columns(
        (
            (pl.col("close") - pl.col("close").mean().over("symbol"))
            / pl.col("close").std().over("symbol")
        ).alias("feat")
    )


def cumulative_to_date_rank(p: pl.DataFrame) -> pl.DataFrame:
    """Ranked against the full history rather than history-to-date."""
    return p.with_columns(pl.col("close").rank("ordinal").over("symbol").alias("feat"))


@pytest.mark.parametrize(
    ("fn", "label"),
    [
        (forward_shift, "forward shift"),
        (centred_window, "centred rolling window"),
        (full_sample_zscore, "full-sample z-score"),
        (cumulative_to_date_rank, "full-history rank"),
    ],
)
def test_lookahead_is_detected(fn, label: str):
    report = lookahead.check(panel(), fn, columns=["feat"])
    assert not report.clean, f"detector missed {label}"
    assert report.failures
    assert "LOOK-AHEAD DETECTED" in report.summary()


def test_assert_helper_raises_on_dirty_feature():
    with pytest.raises(AssertionError, match="LOOK-AHEAD DETECTED"):
        lookahead.assert_no_lookahead(panel(), forward_shift, columns=["feat"])


# --- mechanics ---------------------------------------------------------------


def test_nulls_match_nulls_but_not_values():
    """A feature null in one run and populated in the other is a real mismatch."""

    def null_early(p: pl.DataFrame) -> pl.DataFrame:
        # Populated only when enough history exists in *this* panel, which
        # changes under truncation -- exactly the failure mode to catch.
        return p.with_columns(
            pl.when(pl.len().over("symbol") > 30)
            .then(pl.lit(1.0))
            .otherwise(None)
            .alias("feat")
        )

    report = lookahead.check(panel(), null_early, columns=["feat"])
    assert not report.clean


def test_explicit_cutoffs_are_used():
    cutoffs = [date(2024, 1, 10), date(2024, 1, 20)]
    report = lookahead.check(
        panel(), trailing_return, columns=["feat"], cutoffs=cutoffs
    )
    assert report.checked_cutoffs == cutoffs


def test_too_short_a_panel_raises():
    with pytest.raises(ValueError, match="at least 4 sessions"):
        lookahead.check(panel(n_days=3), trailing_return, columns=["feat"])


def test_missing_feature_column_is_reported():
    report = lookahead.check(panel(), trailing_return, columns=["does_not_exist"])
    assert not report.clean


# --- the real features in this project --------------------------------------


def test_liquidity_features_are_clean():
    """`universe.liquidity` drives selection, so look-ahead there would taint
    every backtest through the universe rather than the signal."""
    lookahead.assert_no_lookahead(
        panel(n_days=60),
        lambda p: uv.liquidity(p, lookback=20),
        columns=["adtv", "coverage"],
    )


def test_reversal_signal_is_clean():
    """The signal used for the Phase 0a calibration (§11a)."""

    def reversal(p: pl.DataFrame) -> pl.DataFrame:
        return p.sort(["symbol", "date"]).with_columns(
            (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1)
            .alias("past_return")
        )

    lookahead.assert_no_lookahead(
        panel(n_days=40), reversal, columns=["past_return"]
    )
