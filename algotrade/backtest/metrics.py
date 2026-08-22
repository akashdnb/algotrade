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

import math
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


def compute(
    equity: pl.DataFrame,
    *,
    periods_per_year: int = TRADING_DAYS,
    risk_free: float = RISK_FREE,
) -> Metrics:
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

    # A first period of exactly -100% makes (1 + net[0]) zero. Total wipeout is
    # a legitimate (if terminal) outcome and must not raise.
    first = float(net[0]) if n else 0.0
    start = (
        float(equity["capital"][0]) / (1 + first) if n and first > -1.0 else 1.0
    )
    total = float(equity["capital"][-1]) / start - 1
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else 0.0

    vol = float(net.std() or 0.0) * (periods_per_year**0.5)
    # `risk_free` must be 0 for a dollar-neutral long/short book: its return is
    # already a self-financing spread, so subtracting rf a second time is a
    # category error worth rf/sigma ~= 0.46 Sharpe units at 14% vol. That single
    # mistake turned momentum_v1's 0.85 into a recorded 0.39 and flipped its gate.
    sharpe = (cagr - risk_free) / vol if vol > 0 else 0.0

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


def after_tax_cagr(
    daily: pl.DataFrame,
    *,
    rate: float = 0.20,
    deferred: bool = False,
    deferred_rate: float = 0.125,
) -> float:
    """CAGR after capital-gains tax, charged out of the book so the reduced base
    compounds.

    Two regimes, and the difference between them is large enough to reorder
    strategies:

    * **Active (default).** A monthly-rebalanced book realises gains
      continuously, so 20% STCG falls due each year on that year's net gain.
      Losses carry forward and offset future gains, as the Act allows -- which
      materially softens the drag after a crash year and must be modelled, or
      volatile strategies are penalised twice.
    * **Deferred (`deferred=True`).** Buy-and-hold pays once on exit, at 12.5%
      LTCG, and the untaxed gains compound in the meantime. The deferral is
      worth more than the lower rate. This is the asymmetry every active book
      has to overcome and it is why an index tracker is a harder benchmark than
      its pre-tax CAGR suggests.

    Args:
        daily: (date, net_return). Annualised on calendar span, not row count.
    """
    if daily.is_empty():
        return 0.0
    days = (daily["date"].max() - daily["date"].min()).days
    years = days / 365.25 if days > 0 else daily.height / TRADING_DAYS
    if years <= 0:
        return 0.0

    if deferred:
        total = float((1 + daily["net_return"]).product())
        after = 1 + (total - 1) * (1 - deferred_rate)
        return after ** (1 / years) - 1 if after > 0 else -1.0

    frame = daily.select(
        pl.col("date").dt.year().alias("yr"), pl.col("net_return")
    ).drop_nulls()
    capital, carried = 1.0, 0.0
    for _, g in frame.group_by("yr", maintain_order=True):
        start = capital
        for r in g["net_return"].to_list():
            capital *= 1 + r
        gain = capital - start
        if gain > 0:
            taxable = max(0.0, gain - carried)
            carried = max(0.0, carried - gain)
            capital -= taxable * rate
        else:
            carried += -gain
    return capital ** (1 / years) - 1 if capital > 0 else -1.0


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
    # A return <= -100% makes log() NaN, rolling_sum propagates it, drop_nulls
    # does NOT drop NaN, and **in polars `NaN > 0` is True** -- so a catastrophic
    # period scored as a pass. [0.1, -1.5, 0.1, 0.1, 0.1] returned a perfect 1.0
    # while the same series with -1.0 returned 0.5. A wiped-out curve could clear
    # the >=0.65 gate outright.
    if bool((equity["net_return"] <= -1.0).any()):
        return 0.0
    rolled = (
        equity.select(
            (1 + pl.col("net_return")).log().rolling_sum(window_size=window).alias("w")
        )
        .drop_nulls()
        .filter(pl.col("w").is_not_nan())
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
    risk_free: float = RISK_FREE,
    positive_fold_fraction: float | None = None,
    pipeline_clean: bool | None = None,
) -> dict:
    """Everything `registry.check_gates` needs, in one call.

    Exists because the gate checker and the metrics used to agree on a dict shape
    only by convention. `rolling_3m_positive` was required by the gates and
    computed nowhere, so `dict.get(..., 0.0)` made that gate **silently always
    fail** -- it would have rejected every hypothesis in Phase 0b. Producing the
    dict here makes the contract code rather than convention.
    """
    m = compute(equity, risk_free=risk_free)
    return {
        "sharpe": m.sharpe,
        # Comes from walkforward.evaluate, which needs a fold count this function
        # has no opinion about. Defaults to 0.0 so an unrun stability check FAILS
        # its gate rather than silently passing.
        "positive_fold_fraction": (
            0.0 if positive_fold_fraction is None else positive_fold_fraction
        ),
        # Same reasoning as positive_fold_fraction: an equity curve cannot
        # reveal whether the pipeline that produced it was causal, so an unrun
        # check defaults to False and FAILS rather than passing by omission.
        "pipeline_clean": False if pipeline_clean is None else bool(pipeline_clean),
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
    risk_free: float = 0.0,
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
    # Align on the date the return was REALISED, not the signal date. An equity
    # row labelled D carries the open(D+1)->open(D+2) return, so joining on D is
    # off by ~2 sessions: a book literally 100% long the market measured beta
    # 0.09 instead of 1.0, making the neutrality diagnostic useless.
    left = equity
    if "exit_date" in equity.columns:
        left = equity.select(
            pl.col("exit_date").alias("date"), "net_return"
        ).drop_nulls("date")
    else:
        left = equity.select("date", "net_return")
    joined = (
        left.join(
            benchmark.select("date", pl.col("ret").alias("bench")),
            on="date",
            how="inner",
        ).drop_nulls()
    )
    # Single return shape, always. An early return with fewer keys made callers
    # KeyError on `beta` -- and in run_hypothesis that happened *after* a holdout
    # look had already been consumed, destroying one of five irreplaceable looks
    # and returning no result.
    if joined.height < 2:
        return {
            "cagr": 0.0,
            "benchmark_cagr": 0.0,
            "excess": 0.0,
            "beats_benchmark": False,
            "beta": 0.0,
            "correlation": 0.0,
            "benchmark_vol": 0.0,
            "overlapping_periods": joined.height,
            "usable": False,
        }

    strat, bench = joined["net_return"], joined["bench"]
    # Calendar span, NOT row count. An inner join against a gappy benchmark keeps
    # the two series aligned -- so beta and correlation stay valid -- but drops
    # rows, and dividing by the survivors annualises a 400-session window as if
    # it were 195 sessions long: benchmark CAGR read 39.1% where the truth was
    # 15.1%. `sprint_risk.py` printed that unguarded.
    span_days = (joined["date"].max() - joined["date"].min()).days
    years = span_days / 365.25 if span_days > 0 else joined.height / periods_per_year

    def _cagr(series: pl.Series) -> float:
        total = float((1 + series).product())
        return total ** (1 / years) - 1 if years > 0 and total > 0 else -1.0

    strat_cagr, bench_cagr = _cagr(strat), _cagr(bench)
    strat_vol = float(strat.std() or 0.0) * (periods_per_year**0.5)
    bench_vol = float(bench.std() or 0.0) * (periods_per_year**0.5)
    bench_var = float(bench.var() or 0.0)
    beta = float(
        pl.DataFrame({"a": strat, "b": bench}).select(pl.cov("a", "b")).item() or 0.0
    ) / bench_var if bench_var > 0 else 0.0

    # Information ratio of the DIFFERENCE series. Reported as a diagnostic, but
    # NOT used as the gate: `IR > 0` is identically `mean(strat) > mean(bench)`
    # -- the standard deviation cancels -- so it carries no risk adjustment at
    # all. It also compares arithmetic means where `excess` compares CAGRs, so
    # the two disagree in sign under volatility drag: 10.6% at 6% vol vs 9.5% at
    # 19.7% vol scored beats_benchmark=False, the exact profile §1 targets.
    diff = strat - bench
    diff_std = float(diff.std() or 0.0)
    ir = (
        float(diff.mean() or 0.0) / diff_std * (periods_per_year**0.5)
        if diff_std > 0
        else 0.0
    )
    if not math.isfinite(ir):
        # inf and nan are truthy, so `or 0.0` never caught them, and they went
        # into trials.jsonl as bare `Infinity`/`NaN` -- not valid JSON.
        ir = 0.0

    return {
        "cagr": strat_cagr,
        "benchmark_cagr": bench_cagr,
        "excess": strat_cagr - bench_cagr,
        # Sharpe DIFFERENCE, which is both risk-adjusted and sign-safe. Ratios of
        # CAGR to vol invert when a CAGR is negative -- a book matching the index
        # at half its volatility scored -0.625 and FAILED while one losing 12% at
        # 60% vol scored -0.200 and PASSED -- so subtract the risk-free rate and
        # compare Sharpes, which orders correctly through zero.
        "beats_benchmark": bool(
            (strat_cagr - risk_free) / strat_vol > (bench_cagr - risk_free) / bench_vol
            if strat_vol > 0 and bench_vol > 0
            else strat_cagr > bench_cagr
        ),
        "information_ratio": ir,
        "beats_benchmark_absolute": strat_cagr > bench_cagr,
        "beta": beta,
        "correlation": float(pl.DataFrame({"a": strat, "b": bench}).select(
            pl.corr("a", "b")
        ).item() or 0.0),
        "benchmark_vol": bench_vol,
        "strategy_vol": strat_vol,
        "overlapping_periods": joined.height,
        "usable": True,
    }
