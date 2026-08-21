"""Cross-sectional long/short construction from a single feature.

One strategy shape, parameterised, rather than a bespoke implementation per
hypothesis. It covers every §4 candidate our data can actually support --
momentum, low-volatility, delivery, reversal -- because they differ only in the
feature and its direction, not in how the book is built.

That matters for §8: a shared constructor means a hypothesis is a *config*, so
the trial log records exactly what was run and two hypotheses cannot
accidentally differ in their portfolio mechanics.

The output is target weights as of each date's **close**, which is what
`backtest.engine` consumes and fills at the next open (§10.5).

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Literal

import polars as pl

Direction = Literal["high", "low"]


def risk_column(panel: pl.DataFrame, *, lookback: int = 60) -> pl.DataFrame:
    """Add `risk`: trailing return volatility, for inverse-vol leg weighting.

    Kept separate from `feature` so a strategy can rank on one quantity and size
    on another -- ranking on momentum while sizing on volatility, for instance.
    """
    daily = pl.col("close") / pl.col("close").shift(1).over("symbol") - 1
    return panel.sort(["symbol", "date"]).with_columns(
        daily.rolling_std(window_size=lookback).over("symbol").alias("risk")
    )


def cross_sectional(
    panel: pl.DataFrame,
    *,
    n_side: int = 15,
    long_side: Direction = "low",
    gross: float = 1.0,
    min_names: int | None = None,
    risk_col: str | None = None,
) -> pl.DataFrame:
    """Rank on `feature` each date; go long one tail, short the other.

    Args:
        panel: Must carry a `feature` column (see `core.features.add`) and be
            restricted to the eligible universe already.
        long_side: `"low"` goes long the lowest-ranked names (reversal,
            low-volatility); `"high"` goes long the highest (momentum). Getting
            this backwards inverts the strategy, so it is explicit rather than
            inferred from the feature's name.
        gross: Total gross exposure. Split evenly across both legs, so the book
            is market-neutral by construction.
        min_names: Dates with fewer eligible names are skipped. Defaults to
            `4 * n_side`, so the two tails are never more than half the
            cross-section -- otherwise "top 15 of 20" is not a tail at all and
            the long and short legs overlap in character.
        risk_col: Column to size inversely to, typically trailing volatility
            from `risk_column`. Equal *weight* is not equal *risk*: momentum's
            winners tend to be higher-beta than its losers, so an equal-weight
            "neutral" book carries a directional bet nobody chose -- which is the
            likeliest explanation for the -26% and -38% drawdowns on nominally
            market-neutral books in §11a. Each leg is normalised separately, so
            the two legs still carry equal gross.

    Returns:
        (date, symbol, weight). Weights sum to ~0 net and `gross` gross.
    """
    if "feature" not in panel.columns:
        raise ValueError("panel needs a 'feature' column -- call features.add first")
    if n_side < 1:
        raise ValueError("n_side must be >= 1")

    floor = 4 * n_side if min_names is None else min_names

    ranked = (
        panel.filter(pl.col("feature").is_not_null())
        .with_columns(
            pl.col("feature").rank("ordinal").over("date").alias("_rank"),
            pl.len().over("date").alias("_n"),
        )
        .filter(pl.col("_n") >= floor)
    )
    if ranked.is_empty():
        return pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
        )

    per_leg = gross / 2
    low_sign = 1.0 if long_side == "low" else -1.0

    low_tail = ranked.filter(pl.col("_rank") <= n_side)
    high_tail = ranked.filter(pl.col("_rank") > pl.col("_n") - n_side)

    legs = []
    for leg, sign in ((low_tail, low_sign), (high_tail, -low_sign)):
        if risk_col is None:
            sized = leg.with_columns(
                (sign * per_leg / pl.len().over("date")).alias("weight")
            )
        else:
            # Inverse-risk within the leg, renormalised so the leg's gross is
            # per_leg regardless of how the risks happen to be distributed.
            inv = (1.0 / pl.col(risk_col)).alias("_inv")
            sized = (
                leg.filter(pl.col(risk_col).is_not_null() & (pl.col(risk_col) > 0))
                .with_columns(inv)
                .with_columns(
                    (
                        sign * per_leg * pl.col("_inv") / pl.col("_inv").sum().over("date")
                    ).alias("weight")
                )
            )
        legs.append(sized.select("date", "symbol", "weight"))

    return pl.concat(legs).sort(["date", "symbol"])


def long_only(
    panel: pl.DataFrame,
    *,
    n: int = 15,
    side: Direction = "high",
    gross: float = 1.0,
) -> pl.DataFrame:
    """Long-only variant: hold one tail, no short leg.

    Worth testing separately rather than assuming the long/short result carries
    over. Shorting Indian equity has real frictions -- borrow availability, and
    intraday-only shorting in the cash segment -- so a long-only version may be
    the only tradeable form of an effect even when the neutral version looks
    better on paper.
    """
    if "feature" not in panel.columns:
        raise ValueError("panel needs a 'feature' column -- call features.add first")

    ranked = panel.filter(pl.col("feature").is_not_null()).with_columns(
        pl.col("feature").rank("ordinal", descending=(side == "high")).over("date").alias("_rank"),
        pl.len().over("date").alias("_n"),
    ).filter(pl.col("_n") >= 2 * n)

    if ranked.is_empty():
        return pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
        )

    return (
        ranked.filter(pl.col("_rank") <= n)
        .with_columns(pl.lit(gross / n).alias("weight"))
        .select("date", "symbol", "weight")
        .sort(["date", "symbol"])
    )


def restrict_to_universe(
    panel: pl.DataFrame, membership: pl.DataFrame
) -> pl.DataFrame:
    """Keep only rows eligible under the point-in-time universe.

    Membership is set monthly (§10.4), so it is carried forward onto every
    session using the most recent rebalance at or before each date -- never a
    later one, which would be look-ahead through the universe rather than the
    signal.
    """
    rebalances = membership.select(pl.col("date").alias("rebal"), "symbol")
    sessions = panel.select("date").unique().sort("date")

    latest = (
        sessions.join(rebalances.select("rebal").unique(), how="cross")
        .filter(pl.col("rebal") <= pl.col("date"))
        .group_by("date")
        .agg(pl.col("rebal").max())
    )
    eligible = latest.join(rebalances, on="rebal").select("date", "symbol")
    return panel.join(eligible, on=["date", "symbol"], how="inner")
