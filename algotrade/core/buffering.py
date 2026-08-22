"""Rank buffering and slower cadences -- turnover control for a known factor.

Momentum's 951%/yr turnover on a *monthly* rebalance is far more churn than a
252-day signal's own persistence implies. The hypothesis this module tests is
that most of it is names oscillating across the portfolio boundary rather than
the signal genuinely changing its mind.

Rank buffering separates the entry and exit thresholds: buy at rank <= `entry`,
but do not sell until rank > `exit_`. A name drifting between 30 and 35 then
generates no trades at all, where a hard top-30 cut would round-trip it every
time it crossed.

Economically this is the cheapest possible turnover reduction, because it
changes *nothing* about the signal -- only about how much evidence is required
to act on a change of mind. That is the property §11a says to look for: it does
not shorten the holding period of a slow signal, it stops paying for noise.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Literal

import polars as pl

Cadence = Literal["daily", "weekly", "monthly", "2monthly", "quarterly"]

_EVERY: dict[str, str] = {
    "weekly": "1w",
    "monthly": "1mo",
    "2monthly": "2mo",
    "quarterly": "1q",
}


def rebalance_dates(dates: list, cadence: Cadence) -> set:
    """Sessions on which positions may change.

    Extends `portfolio.rebalance_schedule` with 2-monthly and quarterly, which
    it does not offer.
    """
    if cadence == "daily":
        return set(dates)
    frame = pl.DataFrame({"date": dates}).sort("date")
    return set(
        frame.group_by_dynamic("date", every=_EVERY[cadence])
        .agg(pl.col("date").max().alias("d"))
        .get_column("d")
        .to_list()
    )


def buffered_long_only(
    panel: pl.DataFrame,
    *,
    entry: int = 30,
    exit_: int = 50,
    cadence: Cadence = "monthly",
    gross: float = 1.0,
    descending: bool = True,
) -> pl.DataFrame:
    """Long-only book with separate entry and exit ranks.

    Args:
        entry: Buy names ranked this high or better.
        exit_: Sell only once a name falls past this rank. Must be >= `entry`;
            equal reproduces a hard cut with no buffer.
        descending: True ranks the *highest* feature value as rank 1 (momentum).

    The book is held at `entry` names. Each rebalance drops anything past
    `exit_`, keeps the rest, and refills from the top of the ranking. Weights are
    equal across whatever is held.

    Necessarily sequential: what to sell depends on what is currently held.
    """
    if "feature" not in panel.columns:
        raise ValueError("panel needs a 'feature' column")
    if exit_ < entry:
        raise ValueError(f"exit_ ({exit_}) must be >= entry ({entry})")

    ranked = (
        panel.filter(pl.col("feature").is_not_null())
        .with_columns(
            pl.col("feature")
            .rank("ordinal", descending=descending)
            .over("date")
            .alias("rank")
        )
        .select("date", "symbol", "rank")
    )
    if ranked.is_empty():
        return pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
        )

    all_dates = ranked["date"].unique().sort().to_list()
    allowed = rebalance_dates(all_dates, cadence)

    # polars yields the group key as a TUPLE, so unpack it -- binding `d` to the
    # tuple made every lookup miss and produced an empty book silently.
    by_date: dict = {
        key[0]: dict(zip(g["symbol"].to_list(), g["rank"].to_list()))
        for key, g in ranked.group_by("date")
    }

    held: list[str] = []
    rows: list[dict] = []
    for day in all_dates:
        ranks = by_date.get(day, {})
        if day in allowed or not held:
            # Keep what is still inside the exit threshold, in rank order.
            kept = sorted(
                (s for s in held if ranks.get(s, 10**9) <= exit_),
                key=lambda s: ranks[s],
            )
            # Refill from the top of the ranking.
            candidates = sorted(
                (s for s, r in ranks.items() if r <= entry and s not in kept),
                key=lambda s: ranks[s],
            )
            held = (kept + candidates)[:entry]
        else:
            # Between rebalances, drop only names that stopped trading.
            held = [s for s in held if s in ranks]

        if not held:
            continue
        w = gross / len(held)
        rows.extend({"date": day, "symbol": s, "weight": w} for s in held)

    if not rows:
        return pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
        )
    return pl.DataFrame(rows).select("date", "symbol", "weight").sort(
        ["date", "symbol"]
    )


def attribute_turnover(blotter: pl.DataFrame) -> pl.DataFrame:
    """Split traded value into entries, exits and rebalances of held names.

    Answers where turnover actually comes from: a book churning its boundary
    looks very different from one resizing stable positions, and the fix differs.
    """
    if blotter.is_empty():
        return pl.DataFrame(
            schema={"kind": pl.String, "value": pl.Float64, "share": pl.Float64}
        )

    ordered = blotter.sort(["symbol", "date"])
    # A buy with no prior position is an entry; a sell that closes out is an
    # exit; anything else resizes an existing holding.
    tagged = ordered.with_columns(
        pl.col("side").shift(1).over("symbol").alias("prev_side"),
        pl.col("date").shift(1).over("symbol").alias("prev_date"),
    ).with_columns(
        pl.when((pl.col("side") == "buy") & pl.col("prev_side").is_null())
        .then(pl.lit("entry (new name)"))
        .when((pl.col("side") == "buy") & (pl.col("prev_side") == "sell"))
        .then(pl.lit("entry (re-entry)"))
        .when(pl.col("side") == "sell")
        .then(pl.lit("exit / trim"))
        .otherwise(pl.lit("add to holding"))
        .alias("kind")
    )
    total = float(tagged["value"].sum())
    return (
        tagged.group_by("kind")
        .agg(pl.col("value").sum().alias("value"), pl.len().alias("trades"))
        .with_columns((pl.col("value") / total).alias("share"))
        .sort("value", descending=True)
    )
