"""Vectorised backtest engine (PROJECT.md §5).

**Fill timing is the single most consequential choice here.** A signal computed
from the close of day D can only be traded at the *open* of D+1, so returns are
measured open-to-open:

    weights from close(D)  ->  position held from open(D+1) to open(D+2)
    realised return        =   open(D+2) / open(D+1) - 1

Filling at close(D) instead is a one-day look-ahead that inflates results
badly, and it does so selectively: for short-horizon reversal signals **most of
the reversion happens in the overnight gap** (§10.5). Fill at close and you
capture that gap; in reality you receive it and capture nothing. Same signal,
opposite conclusion. This is one of the most common reasons Indian retail
backtests lie, so the timing is structural here rather than a parameter.

Costs are charged on the traded delta per symbol per rebalance, using the real
rate card -- including DP, which is flat per scrip and therefore cannot be
approximated as bps (`core.costs`).

Position sizing, vol targeting and turnover damping are deliberately *not* here.
They belong to portfolio construction, so this engine only ever consumes final
target weights.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

import polars as pl

from algotrade.core.costs import (
    RateCard,
    SlippageModel,
    order_charges,
    slippage_bps,
)


@dataclass
class Result:
    """Backtest output. `equity` is the headline; the rest is for diagnosis."""

    equity: pl.DataFrame  # date, capital, gross_return, net_return, cost, turnover
    blotter: pl.DataFrame  # date, symbol, side, value, charges, slippage_bps

    @property
    def final_capital(self) -> float:
        return float(self.equity["capital"][-1]) if self.equity.height else 0.0


def _fill_prices(panel: pl.DataFrame) -> pl.DataFrame:
    """Per symbol, the next session's open and the one after it.

    **Shifts on the session grid, not on row position.** A row-position shift
    reads straight across any hole in a symbol's history -- including the holes
    `adjust.drop_windows` deliberately creates -- and books the whole multi-session
    move as a single "one-day" return. Measured before this fix: 42,727 of
    6,375,325 periods spanned more than one session, the worst compounding
    +8,455% (a 100-session hold), and inside the tradeable universe ASIANPAINT
    booked -89.9% over 4 sessions as one day.

    Periods that are not exactly adjacent are dropped rather than compounded, and
    the realisation dates are emitted so downstream beta can align to when the
    return was actually earned rather than when the signal fired.
    """
    sessions = (
        panel.select("date").unique().sort("date").with_row_index("_sid")
    )
    indexed = panel.join(sessions, on="date", how="left").sort(["symbol", "_sid"])

    shifted = indexed.with_columns(
        pl.col("open").shift(-1).over("symbol").alias("fill_open"),
        pl.col("open").shift(-2).over("symbol").alias("exit_open"),
        pl.col("date").shift(-1).over("symbol").alias("fill_date"),
        pl.col("date").shift(-2).over("symbol").alias("exit_date"),
        pl.col("_sid").shift(-1).over("symbol").alias("_sid1"),
        pl.col("_sid").shift(-2).over("symbol").alias("_sid2"),
        (
            pl.col("adtv").shift(-1).over("symbol")
            if "adtv" in panel.columns
            else pl.lit(None, dtype=pl.Float64)
        ).alias("fill_adtv"),
    )
    return shifted.filter(
        (pl.col("_sid1") == pl.col("_sid") + 1)
        & (pl.col("_sid2") == pl.col("_sid") + 2)
    ).select(
        "date", "symbol", "fill_open", "exit_open", "fill_date", "exit_date",
        "fill_adtv",
    )


def implied_trade_dates(weights: pl.DataFrame) -> set:
    """Sessions on which the TARGET weight vector actually changes.

    A convenience for `run(trade_dates=...)` when the cadence is not to hand.
    `buffered_long_only` and `portfolio.resample` restate identical targets on
    every session between rebalances, so "the target changed" and "a rebalance
    happened" coincide exactly for those books -- including the legitimate
    mid-month case where a name stops trading and the remainder must be
    redeployed.

    Prefer passing the real cadence when you know it. This infers it, and an
    inference is only as good as the assumption that unchanged targets mean
    "hold", not "restore". For a genuinely daily-rebalanced book they mean the
    latter, and this would be wrong -- pass `trade_dates=None` there.
    """
    if weights.is_empty():
        return set()
    per_day = (
        weights.sort(["date", "symbol"])
        .group_by("date", maintain_order=True)
        .agg(
            pl.concat_str(
                pl.col("symbol"), pl.lit(":"), pl.col("weight").round(10).cast(pl.String)
            )
            .str.join("|")
            .alias("book")
        )
        .with_columns(pl.col("book").shift(1).alias("prev"))
    )
    return set(
        per_day.filter(
            pl.col("prev").is_null() | (pl.col("book") != pl.col("prev"))
        )["date"].to_list()
    )


def run(
    panel: pl.DataFrame,
    weights: pl.DataFrame,
    *,
    capital: float,
    card: RateCard,
    slippage: SlippageModel | None = None,
    tier: str = "mid",
    daily_vol: float = 0.02,
    min_trade_value: float = 500.0,
    trade_dates: Iterable[date] | None = None,
) -> Result:
    """Run a backtest over target weights.

    Args:
        panel: Adjusted panel with at least date, symbol, open. An `adtv`
            column, if present, drives slippage.
        weights: (date, symbol, weight) -- targets as of that date's *close*.
            Gross exposure is whatever the weights sum to in absolute value; the
            engine does not normalise them, since that is portfolio
            construction's job.
        capital: Starting capital in rupees.
        slippage: Omit to run frictionless-but-for-charges, which is useful for
            isolating how much of a result the cost model is responsible for.
        min_trade_value: Deltas below this are not traded. Real orders are whole
            share counts and brokers have minimums, so a target that drifts by a
            few rupees produces no trade at all. Without this, charges shrink
            capital, which shifts every `weight x capital` target slightly, which
            manufactures a dust trade every single session -- turnover and cost
            that no real account would incur.
        trade_dates: Sessions on which positions may change. Omit to retarget
            every session.

            **This is load-bearing and its absence was a real defect.** A weight
            row states a target, not an instruction to trade, and the two only
            coincide on a rebalance. `buffered_long_only` and `portfolio.resample`
            restate `gross / len(held)` on *every* session, so once positions
            drift with their own returns the engine trades every day to restore
            exact equal weights. Measured on buffer 30/60 "monthly": 92.9% of
            sessions traded, 86.6% of trades on non-rebalance dates, median trade
            Rs 741 against a flat Rs 15.34 DP charge -- 64% of all cost, for
            drift the signal never asked to correct. Passing the cadence here is
            worth 2.6pp of CAGR and 0.12 Sharpe.

    Returns:
        A `Result` whose equity curve carries gross and net returns separately,
        so cost drag is always visible rather than baked in.
    """
    if weights.is_empty():
        return Result(pl.DataFrame(), pl.DataFrame())

    fills = _fill_prices(panel)
    joined = (
        weights.join(fills, on=["date", "symbol"], how="left")
        # A weight with no fillable price cannot be traded -- typically the last
        # session, or a symbol that stopped trading.
        .filter(pl.col("fill_open").is_not_null() & pl.col("exit_open").is_not_null())
        .filter(pl.col("fill_open") > 0)
        .with_columns(
            (pl.col("exit_open") / pl.col("fill_open") - 1).alias("period_return")
        )
        .sort(["date", "symbol"])
    )
    if joined.is_empty():
        return Result(pl.DataFrame(), pl.DataFrame())

    dates = joined["date"].unique().sort().to_list()
    equity_rows: list[dict] = []
    blotter_rows: list[dict] = []

    running = capital
    previous: dict[str, float] = {}
    may_trade = None if trade_dates is None else set(trade_dates)

    for day in dates:
        slice_ = joined.filter(pl.col("date") == day)
        rets = dict(
            zip(slice_["symbol"].to_list(), slice_["period_return"].to_list())
        )

        # Trades are the change in rupee exposure per symbol.
        target = {
            sym: w * running
            for sym, w in zip(slice_["symbol"].to_list(), slice_["weight"].to_list())
        }
        adtv = dict(zip(slice_["symbol"].to_list(), slice_["fill_adtv"].to_list()))

        cost = 0.0
        traded = 0.0
        held = dict(previous)
        # Between rebalances the book is HELD, not retargeted. `previous` is
        # already marked to market, so the weights have drifted -- that drift is
        # the position taken, not an error to correct. The opening trade always
        # goes through, or nothing is ever bought.
        rebalancing = may_trade is None or day in may_trade or not previous
        for sym in (set(target) | set(previous)) if rebalancing else ():
            delta = target.get(sym, 0.0) - previous.get(sym, 0.0)
            value = abs(delta)
            if value < min_trade_value:
                # Not traded, so the previous exposure carries forward unchanged.
                if sym in previous:
                    held[sym] = previous[sym]
                continue
            held[sym] = target.get(sym, 0.0)
            side = "buy" if delta > 0 else "sell"
            charges = order_charges(value, side, card).total
            slip = 0.0
            if slippage is not None:
                # A missing adtv previously collapsed to 0.0, and slippage_bps
                # then returns half-spread with NO impact term -- the optimistic
                # direction for an unknown input. Unknown liquidity is treated as
                # the worst tier instead.
                liquidity = adtv.get(sym)
                bps = slippage_bps(
                    value,
                    liquidity if liquidity is not None else 0.0,
                    daily_vol,
                    tier if liquidity is not None else "small",
                    slippage,
                )
                slip = value * bps / 10_000
            cost += charges + slip
            traded += value
            blotter_rows.append(
                {
                    "date": day,
                    "symbol": sym,
                    "side": side,
                    "value": value,
                    "charges": charges,
                    "slippage": slip,
                }
            )

        # P&L must come from what is actually HELD, not from the target weights.
        # The dust threshold means the two differ: charging costs on `held` while
        # crediting returns on `weight` books P&L on positions never opened (up
        # to min_trade_value * n_names / capital, ~7.5% of gross at Rs 2L over 30
        # names) and, conversely, gives zero P&L to a carried-forward position
        # absent from today's target.
        gross_return = (
            sum(v * rets.get(s, 0.0) for s, v in held.items()) / running
            if running > 0
            else 0.0
        )
        capital_at_open = running
        cost_return = cost / running if running > 0 else 0.0
        net_return = gross_return - cost_return
        running *= 1 + net_return

        equity_rows.append(
            {
                "date": day,
                # When the return was actually earned, not when the signal fired.
                # Beta and any benchmark join must use this: aligning on the
                # signal date is off by ~2 sessions and measured a literally
                # 100%-long-market book at beta 0.09 instead of 1.0.
                "fill_date": slice_["fill_date"][0],
                "exit_date": slice_["exit_date"][0],
                "capital": running,
                "gross_return": gross_return,
                "net_return": net_return,
                "cost": cost,
                # Sized on pre-return capital, so normalise by the same.
                "turnover": traded / max(capital_at_open, 1e-9),
            }
        )
        # MARK TO MARKET before carrying forward. `held[sym]` is the rupee
        # exposure at the moment of the trade; leaving it static meant next
        # session's `target = w * running` had moved with the P&L while
        # `previous` had not, so `delta` was non-zero even for a book doing
        # nothing. A single buy-and-hold position generated a phantom trade
        # every session, and a "quarterly" 30-name book reported trades on 438
        # of 2,577 sessions. It also missed the real rebalancing driven by
        # cross-sectional dispersion. Net effect was optimistic: understated
        # cost drag by ~1.5pp of CAGR and overstated net Sharpe by ~0.07.
        previous = {
            s: v * (1.0 + rets.get(s, 0.0))
            for s, v in held.items()
            if abs(v) > 0
        }

    return Result(pl.DataFrame(equity_rows), pl.DataFrame(blotter_rows))
