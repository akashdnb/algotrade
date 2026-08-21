"""Performance metrics, including the checks that catch self-deception.

Beyond the usual Sharpe and drawdown, two of these exist specifically to fail
loudly (PROJECT.md §8):

* `trade_concentration` -- if removing the best few periods kills the result,
  there is no edge, just a couple of lucky days.
* `vs_benchmark` -- the benchmark is NIFTYBEES at ~12% CAGR, not zero. A
  strategy netting 14% with 20% drawdowns has *negative* value once bug risk and
  hundreds of hours are priced in.

An in-sample Sharpe above ~2.5 should be read as a bug report, not a discovery.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

TRADING_DAYS: int = 250
# India's risk-free rate, for Sharpe. Roughly the 10y G-sec / liquid fund level.
RISK_FREE: float = 0.065
# Above this, suspect look-ahead rather than celebrate (§8.1).
IMPLAUSIBLE_SHARPE: float = 2.5


@dataclass(frozen=True)
class Metrics:
    periods: int
    total_return: float
    cagr: float
    volatility: float
    sharpe: float
    max_drawdown: float
    hit_rate: float
    avg_turnover: float
    cost_drag: float
    implausible: bool

    def summary(self) -> str:
        flag = "  <-- IMPLAUSIBLE, suspect look-ahead" if self.implausible else ""
        return (
            f"periods       {self.periods:,}\n"
            f"total return  {self.total_return:>8.1%}\n"
            f"CAGR          {self.cagr:>8.1%}\n"
            f"volatility    {self.volatility:>8.1%}\n"
            f"Sharpe        {self.sharpe:>8.2f}{flag}\n"
            f"max drawdown  {self.max_drawdown:>8.1%}\n"
            f"hit rate      {self.hit_rate:>8.1%}\n"
            f"avg turnover  {self.avg_turnover:>8.1%}\n"
            f"cost drag     {self.cost_drag:>8.1%}  (annualised)"
        )


def max_drawdown(capital: pl.Series) -> float:
    """Worst peak-to-trough decline, as a negative fraction."""
    if capital.len() == 0:
        return 0.0
    peak = capital.cum_max()
    return float((capital / peak - 1).min())


def compute(equity: pl.DataFrame, *, periods_per_year: int = TRADING_DAYS) -> Metrics:
    """Metrics from an engine equity curve.

    `cost_drag` is annualised and reported separately from returns, because the
    gross-to-net gap is the whole game at retail scale (§3) and burying it makes
    a strategy look better than it is.
    """
    if equity.is_empty():
        return Metrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, False)

    net = equity["net_return"]
    n = net.len()
    years = n / periods_per_year

    start = float(equity["capital"][0]) / (1 + float(net[0])) if n else 1.0
    total = float(equity["capital"][-1]) / start - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0

    vol = float(net.std() or 0.0) * (periods_per_year**0.5)
    sharpe = (cagr - RISK_FREE) / vol if vol > 0 else 0.0

    gross_ann = float(equity["gross_return"].mean() or 0.0) * periods_per_year
    net_ann = float(net.mean() or 0.0) * periods_per_year

    return Metrics(
        periods=n,
        total_return=total,
        cagr=cagr,
        volatility=vol,
        sharpe=sharpe,
        max_drawdown=max_drawdown(equity["capital"]),
        hit_rate=float((net > 0).mean() or 0.0),
        avg_turnover=float(equity["turnover"].mean() or 0.0),
        cost_drag=gross_ann - net_ann,
        implausible=sharpe > IMPLAUSIBLE_SHARPE,
    )


def rolling_window_positive(
    equity: pl.DataFrame, *, window: int = 63
) -> float:
    """Fraction of rolling windows with a positive cumulative return.

    §8.1 gates on ~65% of rolling 3-month windows being positive. 63 sessions is
    a quarter. This measures consistency rather than total return: a strategy
    that makes everything in two good quarters and loses the rest fails here
    even with an attractive CAGR.

    Returns 0.0 when the history is shorter than one window, so a too-short
    backtest cannot accidentally pass.
    """
    if equity.is_empty() or equity.height < window:
        return 0.0
    # Cumulative log return over each window, via a rolling sum.
    rolled = (
        equity.select(
            (1 + pl.col("net_return")).log().rolling_sum(window_size=window).alias("w")
        )
        .drop_nulls()
    )
    if rolled.is_empty():
        return 0.0
    return float((rolled["w"] > 0).mean() or 0.0)


def gate_inputs(
    equity: pl.DataFrame,
    *,
    benchmark_cagr: float = 0.12,
    drop: int = 5,
    window: int = 63,
) -> dict:
    """Everything `registry.check_gates` needs, in one call.

    Exists because the gate checker and the metrics used to agree on a dict shape
    only by convention. `rolling_3m_positive` was required by the gates and
    computed nowhere, so `dict.get(..., 0.0)` made that gate **silently always
    fail** -- it would have rejected every hypothesis in Phase 0b. Producing the
    dict here makes the contract code rather than convention.
    """
    m = compute(equity)
    return {
        "sharpe": m.sharpe,
        "max_drawdown": m.max_drawdown,
        "rolling_3m_positive": rolling_window_positive(equity, window=window),
        "beats_benchmark": vs_benchmark(equity, benchmark_cagr)["beats_benchmark"],
        "survives_dropping_best_5": trade_concentration(equity, drop=drop)["survives"],
        # Carried through for reporting, not gated on.
        "cagr": m.cagr,
        "volatility": m.volatility,
        "cost_drag": m.cost_drag,
        "avg_turnover": m.avg_turnover,
        "periods": m.periods,
    }


def trade_concentration(equity: pl.DataFrame, drop: int = 5) -> dict[str, float]:
    """Total return with and without the best `drop` periods (§8.1).

    A real edge survives losing its best few days. One that does not is a couple
    of lucky prints wearing a strategy costume.
    """
    if equity.is_empty():
        return {"full": 0.0, "excluding_best": 0.0, "survives": False}

    net = equity["net_return"].to_list()
    full = 1.0
    for r in net:
        full *= 1 + r

    trimmed = sorted(net, reverse=True)[drop:]
    without = 1.0
    for r in trimmed:
        without *= 1 + r

    return {
        "full": full - 1,
        "excluding_best": without - 1,
        "survives": without > 1.0,
    }


def vs_benchmark(
    equity: pl.DataFrame, benchmark_cagr: float = 0.12
) -> dict[str, float]:
    """Compare against buy-and-hold (§1), using an assumed CAGR.

    The benchmark is not zero. Underperforming a click that costs no hours and
    carries no bug risk means the work had negative value.

    Prefer `vs_benchmark_series` when a real index series is available: 0.12 is
    a reasonable long-run assumption but it is not a measurement, and the window
    a strategy is tested over may have been very different.
    """
    m = compute(equity)
    return {
        "cagr": m.cagr,
        "benchmark_cagr": benchmark_cagr,
        "excess": m.cagr - benchmark_cagr,
        "beats_benchmark": m.cagr > benchmark_cagr,
    }


def vs_benchmark_series(
    equity: pl.DataFrame,
    benchmark: pl.DataFrame,
    *,
    periods_per_year: int = TRADING_DAYS,
) -> dict[str, float]:
    """Compare against a measured index series over the *same* window.

    Args:
        benchmark: (date, ret) -- daily benchmark returns, e.g. from
            `io.indices.benchmark_series`.

    **Use an index level, not an ETF.** §11d records why: `NIFTYBEES` split 1:10
    in 2019, ETF actions are absent from the equities feed, and the unadjusted
    series computes to −10% CAGR. An index is a computed level and cannot carry
    that error.

    Also returns beta and correlation. A market-neutral book showing meaningful
    beta is not neutral, whatever its weights sum to — and a large drawdown on a
    nominally neutral strategy is usually this, not bad luck.
    """
    joined = (
        equity.select("date", "net_return")
        .join(benchmark.select("date", pl.col("ret").alias("bench")), on="date", how="inner")
        .drop_nulls()
    )
    if joined.height < 2:
        return {"beats_benchmark": False, "overlapping_periods": joined.height}

    strat, bench = joined["net_return"], joined["bench"]
    years = joined.height / periods_per_year

    def _cagr(series: pl.Series) -> float:
        total = float((1 + series).product())
        return total ** (1 / years) - 1 if years > 0 and total > 0 else -1.0

    strat_cagr, bench_cagr = _cagr(strat), _cagr(bench)
    bench_var = float(bench.var() or 0.0)
    beta = float(
        pl.DataFrame({"a": strat, "b": bench}).select(pl.cov("a", "b")).item() or 0.0
    ) / bench_var if bench_var > 0 else 0.0

    return {
        "cagr": strat_cagr,
        "benchmark_cagr": bench_cagr,
        "excess": strat_cagr - bench_cagr,
        "beats_benchmark": strat_cagr > bench_cagr,
        "beta": beta,
        "correlation": float(pl.DataFrame({"a": strat, "b": bench}).select(
            pl.corr("a", "b")
        ).item() or 0.0),
        "benchmark_vol": float(bench.std() or 0.0) * (periods_per_year**0.5),
        "overlapping_periods": joined.height,
    }
