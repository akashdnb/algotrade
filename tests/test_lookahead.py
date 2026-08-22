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


# --- the two blind spots the audit found ------------------------------------


def test_closure_captured_lookahead_is_caught_with_rebuild():
    """The shape of `run_hypothesis.load_adjusted_panel`.

    Corporate-action factors are derived from the WHOLE panel, so every
    back-adjusted price *level* embeds all future splits. Ratio features cancel
    it; level features do not. Without `rebuild` the factors stay pinned to full
    history and the detector reports clean.
    """
    from algotrade.core import adjust as adj

    p = panel(n_days=40, symbols=("A",))
    days = p["date"].unique().sort().to_list()

    def factors_from(frame: pl.DataFrame) -> pl.DataFrame:
        """A split is only knowable once its ex-date is in the data."""
        ex = days[30]
        rows = [("A", ex, 10.0)] if frame["date"].max() >= ex else []
        return pl.DataFrame(
            rows,
            schema={"symbol": pl.String, "ex_date": pl.Date, "factor": pl.Float64},
            orient="row",
        )

    # A LEVEL feature: absolute price, which the divisor shifts.
    def level_feature(frame: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
        return adj.adjust(frame, factors).with_columns(
            (pl.col("close") > 50).cast(pl.Float64).alias("feat")
        )

    # Without rebuild: factors pinned to the full panel -> invariant -> "clean".
    pinned = factors_from(p)
    blind = lookahead.check(
        p, lambda f: level_feature(f, pinned), columns=["feat"]
    )
    assert blind.clean, "precondition: the blind spot exists without rebuild"

    # With rebuild: factors re-derived per cutoff -> the leak surfaces.
    caught = lookahead.check(
        p,
        level_feature,
        columns=["feat"],
        rebuild=lambda f: {"factors": factors_from(f)},
    )
    assert not caught.clean, "rebuild must expose closure-captured look-ahead"


def test_ratio_features_are_immune_to_the_adjustment_divisor():
    """Why this has not silently poisoned the existing features: a ratio of two
    prices cancels the divisor, so back-adjustment is invisible to it."""
    from algotrade.core import adjust as adj

    p = panel(n_days=40, symbols=("A",))
    days = p["date"].unique().sort().to_list()

    def factors_from(frame: pl.DataFrame) -> pl.DataFrame:
        ex = days[30]
        rows = [("A", ex, 10.0)] if frame["date"].max() >= ex else []
        return pl.DataFrame(
            rows,
            schema={"symbol": pl.String, "ex_date": pl.Date, "factor": pl.Float64},
            orient="row",
        )

    def ratio_feature(frame: pl.DataFrame, factors: pl.DataFrame) -> pl.DataFrame:
        return adj.adjust(frame, factors).sort(["symbol", "date"]).with_columns(
            (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1).alias("feat")
        )

    report = lookahead.check(
        p,
        ratio_feature,
        columns=["feat"],
        rebuild=lambda f: {"factors": factors_from(f)},
    )
    assert report.clean, report.summary()


def test_late_history_peek_is_caught():
    """Default cutoffs were 1/4, 2/4, 3/4 -- nothing after the last was compared.

    Realistic because deliv_pct is null before ~2020, so a delivery feature
    effectively lives only in the recent era.
    """
    p = panel(n_days=40)
    days = p["date"].unique().sort().to_list()

    def late_peek(frame: pl.DataFrame) -> pl.DataFrame:
        return frame.sort(["symbol", "date"]).with_columns(
            pl.when(pl.col("date") >= days[32])
            .then(pl.col("close").shift(-1).over("symbol"))
            .otherwise(pl.col("close"))
            .alias("feat")
        )

    # Interior-only cutoffs miss it; the default now includes a late one.
    interior = lookahead.check(
        p, late_peek, columns=["feat"], cutoffs=[days[10], days[20], days[30]]
    )
    assert interior.clean, "precondition: interior cutoffs cannot see it"
    assert not lookahead.check(p, late_peek, columns=["feat"]).clean


# --- pipeline-level checking (§11h R2b) --------------------------------------


def _weights_from(p: pl.DataFrame, *, drop: set[str] | None = None) -> pl.DataFrame:
    """Minimal stand-in for the research pipeline: rank, take the top 2."""
    scored = p.sort(["symbol", "date"]).with_columns(
        (pl.col("close") / pl.col("close").shift(3).over("symbol") - 1).alias("f")
    ).filter(pl.col("f").is_not_null())
    if drop:
        scored = scored.filter(~pl.col("symbol").is_in(list(drop)))
    ranked = scored.with_columns(
        pl.col("f").rank("ordinal", descending=True).over("date").alias("r")
    )
    return ranked.filter(pl.col("r") <= 2).select(
        "date", "symbol", pl.lit(0.5).alias("weight")
    )


def test_pipeline_check_passes_a_clean_pipeline():
    lookahead.check_pipeline(panel(n_days=40), _weights_from, settle=0)


def test_settle_window_absorbs_the_calendar_rebalance_artefact():
    """A truncated run treats its own last session as a month end.

    `group_by_dynamic` cannot know the month is incomplete, so an extra
    rebalance fires there and the held set legitimately differs. Verified on the
    real pipeline: cutting at 2019-01-03 gave the truncated run a rebalance on
    that date, changing exactly one name. Not look-ahead.
    """
    from algotrade.core import buffering as bufmod

    p = panel(n_days=60)

    def calendar_book(frame: pl.DataFrame) -> pl.DataFrame:
        scored = frame.sort(["symbol", "date"]).with_columns(
            (pl.col("close") / pl.col("close").shift(3).over("symbol") - 1).alias("feature")
        )
        return bufmod.buffered_long_only(scored, entry=2, exit_=3, cadence="monthly")

    days = p["date"].unique().sort().to_list()
    cut = days[40]
    assert lookahead.check_pipeline(p, calendar_book, cutoffs=[cut], settle=25).clean


def test_pipeline_check_catches_a_full_history_exclusion_list():
    """The §11h defect: wiring exclusions.yaml into the universe.

    The list is derived from the WHOLE history, so a name excluded for a late
    event vanishes from the early universe too. `check` cannot see this — the
    exclusion happens outside any feature function.
    """
    # Built explicitly: LATEBLOWUP is well-behaved until session 35, then halves.
    # A full-history exclusion drops it from the ENTIRE sample including 2011-era
    # rows; a truncated run cannot know. The generic fixture would not do -- it
    # flags every symbol early, so truncation changes nothing and the detector
    # has no case to catch.
    start = date(2024, 1, 1)
    rows = []
    for i in range(40):
        for sym, base in (("STEADY_A", 100.0), ("STEADY_B", 90.0), ("STEADY_C", 80.0)):
            close = base + i * (1.0 if sym == "STEADY_A" else 0.5)
            rows.append((start + timedelta(days=i), sym, close, close, close, close, 1e6))
        blow = 100.0 + i if i < 35 else 50.0
        rows.append((start + timedelta(days=i), "LATEBLOWUP", blow, blow, blow, blow, 1e6))
    p = pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "open": pl.Float64,
                "high": pl.Float64, "low": pl.Float64, "close": pl.Float64,
                "turnover": pl.Float64},
        orient="row",
    )

    def leaky(frame: pl.DataFrame) -> pl.DataFrame:
        # "Drop any symbol that ever moved more than 12% in a session",
        # evaluated over whatever history is visible -- which is exactly how a
        # full-history exclusion list behaves under truncation.
        moves = frame.sort(["symbol", "date"]).with_columns(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).abs()
            .alias("m")
        )
        bad = set(moves.filter(pl.col("m") > 0.12)["symbol"].unique().to_list())
        return _weights_from(frame, drop=bad)

    # Precondition: the exclusion set genuinely differs across the boundary.
    assert leaky(p).filter(pl.col("symbol") == "LATEBLOWUP").height == 0
    early = p.filter(pl.col("date") <= start + timedelta(days=20))
    assert leaky(early).filter(pl.col("symbol") == "LATEBLOWUP").height > 0

    # settle=0: `_weights_from` emits weights every session with no calendar
    # rebalance, so there is no month-end artefact to absorb. The default 25
    # exists for calendar-anchored books and would swallow a leak injected into a
    # 40-session fixture.
    report = lookahead.check_pipeline(
        p, leaky, cutoffs=[start + timedelta(days=20)], settle=0
    )
    assert not report.clean, "pipeline check must catch a full-history exclusion"


def test_pipeline_check_catches_a_forward_looking_regime_mask():
    """A regime filter that peeks at the next session's market direction."""
    p = panel(n_days=40)

    def leaky(frame: pl.DataFrame) -> pl.DataFrame:
        w = _weights_from(frame)
        mkt = (
            frame.group_by("date").agg(pl.col("close").mean().alias("mkt")).sort("date")
            .with_columns((pl.col("mkt").shift(-1) > pl.col("mkt")).alias("up_next"))
        )
        keep = set(mkt.filter(pl.col("up_next"))["date"].to_list())
        return w.filter(pl.col("date").is_in(list(keep)))

    assert not lookahead.check_pipeline(p, leaky, settle=0).clean


def test_feature_level_check_is_blind_to_both():
    """Why R2b exists: the feature itself is clean in both cases above."""
    lookahead.assert_no_lookahead(
        panel(n_days=40),
        lambda f: f.sort(["symbol", "date"]).with_columns(
            (pl.col("close") / pl.col("close").shift(3).over("symbol") - 1).alias("feat")
        ),
        columns=["feat"],
    )


def test_pipeline_check_needs_enough_history():
    with pytest.raises(ValueError, match="at least 8 sessions"):
        lookahead.check_pipeline(panel(n_days=5), _weights_from)
