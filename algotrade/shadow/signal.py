"""The deployed target signal: the momentum sleeve (PROJECT.md §11o).

**Scope: momentum only.** §11o deploys 50% momentum / 50% NIFTYBEES, and the
NIFTYBEES half is held manually outside this system -- it is never tracked,
traded, or rebalanced here. So this module targets a fully-invested momentum
book, and the 50% allocation is a property of the *account*, not of these
weights.

No new signal-generation logic -- this composes the exact functions the
research scripts use (`scripts/remeasure_finalists.py`'s `sleeve()`), so there
is no second implementation of the strategy to drift from the one the backtest
validated.

Frozen parameters (§10.4, §11o): do not tune. Changing any of them is a new
strategy version and forks a new portfolio rather than mutating this one.

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

STRATEGY_VERSION: str = "momentum_v1"


def deployed_weights(panel: pl.DataFrame) -> pl.DataFrame:
    """Target weights for the momentum sleeve, for every session in `panel`.

    Weights sum to 1.0 across the ~30 held names -- the sleeve is fully
    invested in itself. Scaling to the account's momentum allocation is the
    caller's job.

    Args:
        panel: Adjusted panel with at least date, symbol, close, turnover.
    """
    liq = universe.liquidity(panel)
    members = universe.membership(liq, exclude=set(universe.DEFAULT_EXCLUSIONS))
    eligible = strategies.restrict_to_universe(
        features.add(liq, "momentum", lookback=MOMENTUM_LOOKBACK, skip=MOMENTUM_SKIP),
        members,
    )
    return buffering.buffered_long_only(
        eligible, entry=ENTRY, exit_=EXIT, cadence=CADENCE, descending=True
    )
