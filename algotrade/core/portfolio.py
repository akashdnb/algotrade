"""Portfolio construction: sizing, vol targeting, turnover control (§5).

The strategy emits a pure opinion about the market. Everything about *how much*
and *how to get from here to there* lives here, so a strategy works unchanged at
Rs 2L or Rs 20L.

Two pieces earn their place from evidence rather than convention:

**Vol targeting is architecture, not a feature** (§5). The endgame is 2-2.5x
leverage, and leverage requires a *known* volatility. Strategies built without
it are usually unleverageable and you discover that two years in.

**Turnover control is the highest-value lever in this project.** The Phase 0a
calibration measured a 29.4% annual cost drag turning a real +8.5% gross edge
into -20.8% net (§11a). Daily rebalancing at ~30 bps a round trip is fatal. That
is not a preference -- it is the boundary between viable and dead.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

from typing import Final, Literal

import polars as pl

# Target portfolio volatility. Low by design: a low-vol strategy can be levered
# up, while a high-vol one cannot be levered down into a good one (§1).
VOL_TARGET: Final[float] = 0.08
VOL_LOOKBACK: Final[int] = 60
MAX_LEVERAGE: Final[float] = 2.5
MAX_WEIGHT: Final[float] = 0.20

Cadence = Literal["daily", "weekly", "monthly"]


def cap_weights(
    weights: pl.DataFrame,
    *,
    max_weight: float = MAX_WEIGHT,
    max_passes: int = 20,
) -> pl.DataFrame:
    """Clip per-name weights to `max_weight`, redistributing the excess.

    Water-filling rather than clip-then-renormalise. Renormalising the whole
    book after clipping scales the capped names back *above* the cap, so the
    excess is instead pushed only onto names still below it, repeatedly until
    either nothing is over the cap or no headroom remains.

    If the cap is too tight to absorb the original gross (`max_weight` times the
    number of names is less than gross), the book ends at the cap everywhere and
    gross is necessarily lower -- which is the honest outcome.
    """
    if weights.is_empty():
        return weights

    target_gross = weights.group_by("date").agg(
        pl.col("weight").abs().sum().alias("_target")
    )
    out = weights.join(target_gross, on="date", how="left").with_columns(
        # Remembered up front: a weight driven to zero mid-loop would otherwise
        # lose the sign that says which leg it belongs to.
        # +1 for a genuinely-zero weight would push long exposure into names the
        # strategy never selected: [0.5, -0.5, 0, 0] came back [0.2, -0.2, 0.2,
        # 0.2], turning a dollar-neutral book 40% net long. Zero stays zero.
        pl.when(pl.col("weight") > 0).then(1.0)
        .when(pl.col("weight") < 0).then(-1.0)
        .otherwise(0.0).alias("_leg_sign")
    )

    for _ in range(max_passes):
        out = out.with_columns(
            pl.col("weight").clip(-max_weight, max_weight).alias("weight")
        )
        out = out.with_columns(
            pl.col("weight").abs().sum().over("date").alias("_gross"),
            # Headroom exists only on names not already at the cap.
            (max_weight - pl.col("weight").abs()).alias("_room"),
        )
        out = out.with_columns(pl.col("_room").sum().over("date").alias("_total_room"))
        shortfall = pl.col("_target") - pl.col("_gross")
        if float(out.select(shortfall.max()).item() or 0.0) <= 1e-12:
            break
        out = out.with_columns(
            pl.when(
                (pl.col("_total_room") > 1e-12)
                & (pl.col("_room") > 0)
                & (pl.col("_leg_sign") != 0)   # never fund a name from nothing
            )
            .then(
                pl.col("weight")
                # A zero weight has sign 0, so it could never receive its share
                # and the book converged below target despite having capacity.
                # Fall back to the leg's own sign.
                + pl.when(pl.col("weight") != 0)
                .then(pl.col("weight").sign())
                .otherwise(pl.col("_leg_sign"))
                * shortfall
                * pl.col("_room")
                / pl.col("_total_room")
            )
            .otherwise(pl.col("weight"))
            .alias("weight")
        )

    # Final clip: the loop clips at the TOP of each pass, so exhausting
    # max_passes right after a redistribution could return over-cap weights.
    return out.with_columns(
        pl.col("weight").clip(-max_weight, max_weight)
    ).select("date", "symbol", "weight")


def rebalance_schedule(dates: list, cadence: Cadence) -> set:
    """Which sessions are allowed to change positions.

    The cheapest turnover reduction available: holding weights between
    rebalances cuts trade count roughly proportionally, and cost is per trade.
    """
    if cadence == "daily":
        return set(dates)
    frame = pl.DataFrame({"date": dates}).sort("date")
    every = "1w" if cadence == "weekly" else "1mo"
    return set(
        frame.group_by_dynamic("date", every=every)
        .agg(pl.col("date").max().alias("d"))
        .get_column("d")
        .to_list()
    )


def resample(weights: pl.DataFrame, cadence: Cadence = "weekly") -> pl.DataFrame:
    """Hold weights constant between rebalance dates.

    Turnover falls roughly in proportion to the cadence, and so does cost drag.
    On the Phase 0a calibration this is the difference between -21% and roughly
    break-even (§11a).
    """
    if weights.is_empty() or cadence == "daily":
        return weights

    dates = weights["date"].unique().sort().to_list()
    allowed = rebalance_schedule(dates, cadence)

    held: pl.DataFrame | None = None
    out: list[pl.DataFrame] = []
    for day in dates:
        if day in allowed or held is None:
            held = weights.filter(pl.col("date") == day).select("symbol", "weight")
        out.append(held.with_columns(pl.lit(day).cast(pl.Date).alias("date")))
    return pl.concat(out).select("date", "symbol", "weight")


def apply_no_trade_band(
    weights: pl.DataFrame, *, band: float = 0.02
) -> pl.DataFrame:
    """Keep the existing weight when the target moves less than `band`.

    Complements `resample`: that controls *when* trades may happen, this
    controls *whether* a small drift is worth paying for. Necessarily sequential,
    since each day depends on what was actually held.
    """
    if weights.is_empty() or band <= 0:
        return weights

    dates = weights["date"].unique().sort().to_list()
    current: dict[str, float] = {}
    out: list[dict] = []
    for day in dates:
        rows = weights.filter(pl.col("date") == day)
        target = dict(zip(rows["symbol"].to_list(), rows["weight"].to_list()))
        held: dict[str, float] = {}
        for sym in set(target) | set(current):
            want = target.get(sym, 0.0)
            have = current.get(sym, 0.0)
            # Exiting entirely is always allowed; only adjustments are damped.
            held[sym] = have if (want != 0.0 and abs(want - have) < band) else want
        current = {s: w for s, w in held.items() if w != 0.0}
        out.extend({"date": day, "symbol": s, "weight": w} for s, w in current.items())

    if not out:
        return weights.head(0)
    return pl.DataFrame(out).select("date", "symbol", "weight").sort(["date", "symbol"])


def vol_scalar(
    returns: pl.DataFrame,
    *,
    target: float = VOL_TARGET,
    lookback: int = VOL_LOOKBACK,
    max_leverage: float = MAX_LEVERAGE,
    periods_per_year: int = 250,
) -> pl.DataFrame:
    """Leverage per date so realised vol tracks `target`.

    Args:
        returns: (date, net_return) from an unlevered backtest.

    The trailing window **excludes the current period** -- scaling today's
    position by a volatility that includes today's return is look-ahead, and a
    subtle one because the result still looks plausible.

    Leverage is null until the window fills, so the caller must decide whether
    to skip those sessions or run unlevered.
    """
    if returns.is_empty():
        return returns.with_columns(pl.lit(None, dtype=pl.Float64).alias("leverage"))

    return (
        returns.sort("date")
        .with_columns(
            # shift(2), not 1. The engine labels each row by the SIGNAL date D
            # while net_return[D] spans open(D+1)->open(D+2), so net_return[D-1]
            # is not observable at the close of D. shift(1) leaked one period.
            pl.col("net_return")
            .shift(2)
            .rolling_std(window_size=lookback)
            .alias("_vol")
        )
        .with_columns((pl.col("_vol") * (periods_per_year**0.5)).alias("realised_vol"))
        .with_columns(
            pl.when(pl.col("realised_vol") > 0)
            .then((target / pl.col("realised_vol")).clip(0.0, max_leverage))
            .otherwise(None)
            .alias("leverage")
        )
        .drop("_vol")
    )


def scale(weights: pl.DataFrame, leverage: pl.DataFrame) -> pl.DataFrame:
    """Apply a per-date leverage series to weights.

    Dates with no leverage yet (the vol window still filling) are dropped rather
    than run unlevered, so the equity curve never mixes two sizing regimes.
    """
    return (
        weights.join(leverage.select("date", "leverage"), on="date", how="inner")
        .filter(pl.col("leverage").is_not_null())
        .with_columns((pl.col("weight") * pl.col("leverage")).alias("weight"))
        .select("date", "symbol", "weight")
    )


def gross_exposure(weights: pl.DataFrame) -> pl.DataFrame:
    """Gross and net exposure per date -- a sanity check on construction."""
    return (
        weights.group_by("date")
        .agg(
            pl.col("weight").abs().sum().alias("gross"),
            pl.col("weight").sum().alias("net"),
            pl.len().alias("positions"),
        )
        .sort("date")
    )
