"""The deployed target signal: 50/50 Momentum + Low-Vol (PROJECT.md §11o).

No new signal-generation logic -- this composes the exact functions the
research scripts use (`scripts/remeasure_finalists.py`'s `sleeve()`), so
there is no second implementation of the strategy to drift from the one the
backtest validated.

Both sleeves share cadence (monthly, §10.4) and tax treatment (both are
short-term equity holdings -- unlike the Momentum+NIFTY variant, whose index
leg needed separate LTCG-deferred treatment, §11q finding F3). That means
they can be combined into a single (date, symbol, weight) target and run
through one `engine.run` call, rather than tracked as two capital
sub-accounts with an inter-sleeve rebalance -- simpler, and equivalent for
this specific blend because nothing about it depends on sleeve-level tax
differentiation.

Pure module: DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

import polars as pl

from algotrade.core import buffering, features, strategies, universe

ENTRY: int = 30
EXIT: int = 60
CADENCE = "monthly"
MOMENTUM_LOOKBACK: int = 252
MOMENTUM_SKIP: int = 21
LOW_VOL_LOOKBACK: int = 60


def combine_sleeves(*sleeves: tuple[pl.DataFrame, float]) -> pl.DataFrame:
    """Blend allocated sleeves into one target vector.

    Args:
        sleeves: Each is `(weights, allocation)`, where `weights` is
            (date, symbol, weight) and `allocation` is the fraction of gross
            capital assigned to that sleeve. A symbol held by more than one
            sleeve on the same date has its scaled weights summed, not
            overwritten -- the two sleeves genuinely coexist in one book.
    """
    scaled = [
        w.with_columns((pl.col("weight") * allocation).alias("weight"))
        for w, allocation in sleeves
        if w.height
    ]
    if not scaled:
        return pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
        )
    return (
        pl.concat(scaled)
        .group_by(["date", "symbol"])
        .agg(pl.col("weight").sum())
        .sort(["date", "symbol"])
    )


def deployed_weights(panel: pl.DataFrame) -> pl.DataFrame:
    """Target weights for the deployed strategy, for every session in `panel`.

    Args:
        panel: Adjusted panel with at least date, symbol, close, turnover.
    """
    liq = universe.liquidity(panel)
    members = universe.membership(liq, exclude=set(universe.DEFAULT_EXCLUSIONS))

    momentum_elig = strategies.restrict_to_universe(
        features.add(liq, "momentum", lookback=MOMENTUM_LOOKBACK, skip=MOMENTUM_SKIP),
        members,
    )
    low_vol_elig = strategies.restrict_to_universe(
        features.add(liq, "realised_vol", lookback=LOW_VOL_LOOKBACK), members
    )

    momentum_weights = buffering.buffered_long_only(
        momentum_elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=True
    )
    low_vol_weights = buffering.buffered_long_only(
        low_vol_elig, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=False
    )
    return combine_sleeves((momentum_weights, 0.5), (low_vol_weights, 0.5))
