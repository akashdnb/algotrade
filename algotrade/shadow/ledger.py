"""Paper P&L for the shadow portfolio: shadow_full (every recommendation taken
blindly) and benchmark (NIFTY buy-and-hold), per PROJECT.md §9.

No `live` curve and no discretion layer -- there is no accept/decline yet to
diverge from `shadow_full`, since no real money is committed (deferred to
Phase 0d / full §9).
"""

from __future__ import annotations

import polars as pl

from algotrade.backtest import engine
from algotrade.core.costs import RateCard, SlippageModel

SHADOW_CURVE_SCHEMA = {"date": pl.Date, "nav": pl.Float64, "net_return": pl.Float64}
BENCHMARK_CURVE_SCHEMA = {"date": pl.Date, "nav": pl.Float64}


def shadow_full_curve(
    weights: pl.DataFrame,
    panel: pl.DataFrame,
    *,
    capital: float,
    card: RateCard,
    slippage: SlippageModel | None = None,
    tier: str = "mid",
) -> pl.DataFrame:
    """(date, nav, net_return) for taking every recommendation blindly.

    `date` is `exit_date` -- when the return was actually earned, not when the
    signal fired (the same convention `engine.run`'s own docstring requires
    for beta/benchmark alignment).
    """
    if weights.is_empty():
        return pl.DataFrame(schema=SHADOW_CURVE_SCHEMA)

    result = engine.run(
        weights=weights,
        panel=panel,
        capital=capital,
        card=card,
        slippage=slippage,
        tier=tier,
        trade_dates=engine.implied_trade_dates(weights),
    )
    if result.equity.is_empty():
        return pl.DataFrame(schema=SHADOW_CURVE_SCHEMA)

    return (
        result.equity.select(
            pl.col("exit_date").alias("date"),
            pl.col("capital").alias("nav"),
            "net_return",
        )
        .sort("date")
    )


def benchmark_curve(index_returns: pl.DataFrame, *, capital: float) -> pl.DataFrame:
    """(date, nav) compounding `capital` forward through an index return series.

    `index_returns` is (date, ret) -- e.g. `algotrade.io.indices.benchmark_series`'s
    output. Null returns (no matching session) are dropped rather than treated
    as zero, which would silently understate compounding.
    """
    ordered = index_returns.drop_nulls("ret").sort("date") if index_returns.height else index_returns
    if ordered.is_empty():
        return pl.DataFrame(schema=BENCHMARK_CURVE_SCHEMA)
    return ordered.select(
        "date",
        (capital * (1 + pl.col("ret")).cum_prod()).alias("nav"),
    )


def build_ledger(shadow: pl.DataFrame, benchmark: pl.DataFrame) -> pl.DataFrame:
    """Join the two curves on their common sessions.

    Inner join: a date either book lacks (e.g. shadow's warm-up before its
    first fillable session) is not comparable and is dropped rather than
    filled, which would invent a return that was never measured.
    """
    return (
        shadow.select(
            "date",
            pl.col("nav").alias("shadow_full_nav"),
            pl.col("net_return").alias("shadow_full_return"),
        )
        .join(
            benchmark.select("date", pl.col("nav").alias("benchmark_nav")),
            on="date",
            how="inner",
        )
        .sort("date")
    )
