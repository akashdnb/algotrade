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

from dataclasses import dataclass

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

    Both are needed because a weight decided on close(D) is filled at open(D+1)
    and marked out at open(D+2).
    """
    return (
        panel.sort(["symbol", "date"])
        .with_columns(
            pl.col("open").shift(-1).over("symbol").alias("fill_open"),
            pl.col("open").shift(-2).over("symbol").alias("exit_open"),
            pl.col("adtv").shift(-1).over("symbol").alias("fill_adtv")
            if "adtv" in panel.columns
            else pl.lit(None, dtype=pl.Float64).alias("fill_adtv"),
        )
        .select("date", "symbol", "fill_open", "exit_open", "fill_adtv")
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

    for day in dates:
        slice_ = joined.filter(pl.col("date") == day)

        gross_return = float(
            (slice_["weight"] * slice_["period_return"]).sum()
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
        for sym in set(target) | set(previous):
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
                bps = slippage_bps(
                    value, adtv.get(sym) or 0.0, daily_vol, tier, slippage
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

        cost_return = cost / running if running > 0 else 0.0
        net_return = gross_return - cost_return
        running *= 1 + net_return

        equity_rows.append(
            {
                "date": day,
                "capital": running,
                "gross_return": gross_return,
                "net_return": net_return,
                "cost": cost,
                "turnover": traded / max(running, 1e-9),
            }
        )
        # Carry forward what is actually held, not what was targeted -- they
        # differ wherever a delta fell below the dust threshold.
        previous = {s: v for s, v in held.items() if abs(v) > 0}

    return Result(pl.DataFrame(equity_rows), pl.DataFrame(blotter_rows))
