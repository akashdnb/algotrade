"""Mark the book to market and write the daily snapshot.

**Prices come from the most recent session at or before the valuation date.**
That is what makes a weekend or holiday valuation correct rather than
impossible: Saturday's portfolio is worth exactly what Friday's closes say it
is. Carrying prices forward is not an approximation here -- no trading
happened, so nothing changed.

A snapshot is a pure function of (holdings, prices, ledger, contributions) on a
date, so writing it is an upsert and re-running is safe. That is what makes
`recover.yml` idempotent over a range.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Iterable, Mapping

from algotrade.domain import money, valuation as val
from algotrade.domain.models import Holding, Position, Snapshot

log = logging.getLogger(__name__)


class NoPricesAvailable(RuntimeError):
    """No session on or before the valuation date has prices."""


@dataclass(frozen=True, slots=True)
class Valuation:
    portfolio_id: str
    date: date
    price_date: date
    positions: list[Position]

    market_value: Decimal
    cash: Decimal
    total_value: Decimal

    invested_capital: Decimal
    realised_pnl: Decimal
    unrealised_pnl: Decimal

    units: Decimal
    unit_nav: Decimal
    peak_unit_nav: Decimal
    drawdown: float
    total_return: float | None
    day_return: float | None
    benchmark_close: Decimal | None = None

    @property
    def total_pnl(self) -> Decimal:
        return money.rupees(self.realised_pnl + self.unrealised_pnl)


class ValuationService:
    def __init__(self, *, snapshots, cash, holdings=None, audit=None) -> None:
        self._snapshots = snapshots
        self._cash = cash
        self._holdings = holdings
        self._audit = audit

    def value(
        self,
        *,
        portfolio_id: str,
        as_of: date,
        holdings: list[Holding],
        prices: dict[str, Decimal],
        price_date: date,
        units: Decimal,
        total_contributed: Decimal,
        realised_pnl: Decimal = money.ZERO,
        benchmark_close: Decimal | None = None,
        cash: Decimal | None = None,
    ) -> Valuation:
        """Compute the valuation. No writes.

        Args:
            cash: Defaults to the LEDGER balance, not a cached field -- the
                ledger is the authority and this is where that matters most.
            price_date: The session the prices came from. Recorded so a
                weekend snapshot is visibly carried forward rather than
                silently stale.
        """
        balance = (
            self._cash.balance(portfolio_id, through=as_of)
            if cash is None else cash
        )

        # Weights are against total value, so cash drag is visible instead of
        # the positions summing to 1.0 regardless of how much is uninvested.
        provisional = money.sum_rupees(
            h.value_at(prices.get(h.symbol, money.ZERO)) for h in holdings
        )
        positions, market_value = val.value_positions(
            holdings, prices, nav=money.rupees(provisional + balance)
        )
        total = money.rupees(market_value + balance)

        cost_basis = money.sum_rupees(h.cost_basis for h in holdings)
        unrealised = money.rupees(market_value - cost_basis)

        unit_nav = val.unit_nav(nav=total, units=units)
        # Floored at BASE_UNIT_NAV, never at zero. `units` is
        # `initial_capital / BASE_UNIT_NAV`, so a portfolio is worth exactly
        # 100/unit at inception and the running peak cannot honestly start
        # below that. Seeding from an empty collection gave ZERO, so the first
        # snapshot's peak became its own unit NAV and its drawdown was 0 by
        # construction -- which hid a real 1.6% loss on the first session this
        # book was valued (found 2026-09-05).
        previous_peak = self._snapshots.peak_unit_nav(portfolio_id) or money.ZERO
        peak = val.new_peak(unit_nav=unit_nav,
                            peak=max(previous_peak, val.BASE_UNIT_NAV))

        previous = self._snapshots.latest(portfolio_id)
        day_return = (
            money.percent_change(unit_nav, previous.unit_nav)
            if previous is not None and previous.date < as_of
            and previous.unit_nav > 0 else None
        )
        total_return = (
            float((unit_nav - val.BASE_UNIT_NAV) / val.BASE_UNIT_NAV)
            if unit_nav > 0 else None
        )

        return Valuation(
            portfolio_id=portfolio_id, date=as_of, price_date=price_date,
            positions=positions,
            market_value=market_value, cash=balance, total_value=total,
            invested_capital=cost_basis,
            realised_pnl=realised_pnl, unrealised_pnl=unrealised,
            units=units, unit_nav=unit_nav, peak_unit_nav=peak,
            drawdown=val.drawdown(unit_nav=unit_nav, peak=peak),
            total_return=total_return, day_return=day_return,
            benchmark_close=benchmark_close,
        )

    def snapshot(
        self, valuation: Valuation, *, now: datetime,
        total_contributed: Decimal, panel_manifest_hash: str = "",
    ) -> Snapshot:
        """Persist. Upsert by (portfolio, date), so re-running is a no-op."""
        snapshot = Snapshot(
            portfolio_id=valuation.portfolio_id,
            date=valuation.date,
            nav=valuation.total_value,
            cash=valuation.cash,
            invested_value=valuation.market_value,
            units=valuation.units,
            unit_nav=valuation.unit_nav,
            total_contributed=total_contributed,
            positions=valuation.positions,
            peak_unit_nav=valuation.peak_unit_nav,
            drawdown=valuation.drawdown,
            benchmark_close=valuation.benchmark_close,
            panel_manifest_hash=panel_manifest_hash,
            computed_at=now,
        )
        self._snapshots.save_valuation(snapshot, valuation)
        return snapshot


#: The benchmark leg, as a TRADEABLE proxy rather than an index level.
#:
#: `sec_bhavdata_full` carries equities and ETFs, not index levels, so the
#: NIFTY 50 itself is not in the panel -- and NIFTYBEES is what
#: INVESTMENT-POLICY.md actually allocates 50% to, so comparing against it is
#: comparing against the thing you would otherwise hold. It tracks the index
#: net of its own expense ratio, which is the honest comparison: an investor
#: cannot buy the index, only a fund that follows it.
BENCHMARK_SYMBOL = "NIFTYBEES"


def valuable_dates(targets: Iterable[date], inception: date | None) -> list[date]:
    """The subset of `targets` on which the portfolio actually existed.

    `--backfill N` marks the CURRENT holdings at each target's prices. For a
    genuinely missed recent session that is correct -- the book did not change
    in between. For a date before the book existed it is not an approximation,
    it is fiction: the first daily run produced snapshots showing all thirty
    positions and zero cash six days before the first execution, and those rows
    went on to anchor the drawdown peak to a high the portfolio never reached.

    `None` -- no ledger entry at all -- yields nothing. A portfolio that has
    never been funded cannot honestly be valued on any date.
    """
    if inception is None:
        return []
    return [day for day in targets if day >= inception]


def merge_prices(
    ranked: Mapping[str, Decimal],
    held: Iterable[str],
    panel: Mapping[str, Decimal],
) -> tuple[dict[str, Decimal], list[str]]:
    """Every price a rebalance decision needs, and what could not be priced.

    Ranked names keep the close **the ranking itself used**, so sizing and
    selection cannot disagree across a panel rebuild. Held names outside the
    ranking are priced from the panel.

    **Never defaults to zero.** The signal set stores only the top `exit` ranks
    -- 60 of ~2,600 traded symbols -- so a held name missing from it has almost
    always just fallen out of the buffer, which is *why it is being sold* and
    says nothing about what it is worth. Zeroing it made every sell instruction
    read `@ Rs 0.00` and dropped the sale proceeds out of investable capital.

    A name that genuinely cannot be priced is returned in the second element
    for the caller to refuse on. A sell at zero is not a conservative estimate,
    it is a wrong one, and it is the shape a human would have caught before the
    shadow portfolio began auto-filling at the reference price.
    """
    prices = dict(ranked)
    unpriced: set[str] = set()
    for symbol in held:
        if symbol in prices:
            continue
        close = panel.get(symbol)
        # A zero from the panel is the very value this refuses to invent, so it
        # cannot be allowed back in through the fallback.
        if close is None or close <= 0:
            unpriced.add(symbol)
        else:
            prices[symbol] = close
    return prices, sorted(unpriced)


#: How far back a symbol's last close may be carried forward.
#:
#: ~6 weeks of trading. Long enough to cover a name suspended for a corporate
#: action or moved between settlement series, short enough that a genuinely
#: delisted stock stops holding phantom value: past this the symbol is left
#: UNPRICED so it surfaces, rather than marked at a price nobody could get.
MAX_STALE_SESSIONS: int = 30


def prices_as_of(
    panel, *, as_of: date, symbols: set[str] | None = None
) -> tuple[dict[str, Decimal], date, dict[str, date]]:
    """Closes from the latest session at or before `as_of`, carried per symbol.

    Returns `(prices, price_date, stale)`, where `stale` maps a symbol to the
    date its price actually came from -- present only for symbols that did not
    trade on `price_date`.

    Two different carry-forwards, and both are needed:

    * **By date.** A weekend or holiday resolves to the previous trading day.
      Correct rather than approximate: nothing traded, so nothing moved.
    * **By symbol.** A name absent from `price_date` keeps its own last close.
      Without this, one symbol missing from one session was simply absent from
      the dict, and `value_positions` carried it at ZERO.

    That second gap is not hypothetical. NSE moved HFCL from series `EQ` to
    `BE` on 2026-09-03; the panel keeps `EQ` only, so a stock trading four lakh
    shares a day at Rs 231.44 vanished and was marked to zero -- understating
    NAV by Rs 6,943 and writing a fake 3.5-point drawdown into the permanent
    snapshot series.

    Staleness is **returned, not hidden**. A carried price is a real estimate
    but it is not a quote, and a caller that cannot tell the difference will
    eventually report a suspended holding as though it were liquid.

    Carry-forward needs an explicit `symbols` set. With none given there is no
    book to value, and scanning ~2,600 symbols for gaps would be work done for
    nobody.

    Raises:
        NoPricesAvailable: nothing on or before the date.
    """
    import polars as pl

    sessions = sorted(d for d in panel["date"].unique().to_list() if d <= as_of)
    if not sessions:
        raise NoPricesAvailable(f"no session on or before {as_of}")
    price_date = sessions[-1]

    day = panel.filter(pl.col("date") == price_date)
    if symbols is not None:
        day = day.filter(pl.col("symbol").is_in(list(symbols)))
    prices = {
        r["symbol"]: money.rupees(str(r["close"]))
        for r in day.iter_rows(named=True)
        if r.get("close") is not None
    }

    stale: dict[str, date] = {}
    missing = (symbols - set(prices)) if symbols else set()
    if missing:
        # Bounded by sessions, not calendar days: a holiday-heavy stretch must
        # not consume the allowance any faster than a busy one.
        floor = sessions[max(0, len(sessions) - 1 - MAX_STALE_SESSIONS)]
        recent = (
            panel.filter(
                pl.col("symbol").is_in(list(missing))
                & (pl.col("date") <= price_date)
                & (pl.col("date") >= floor)
                & pl.col("close").is_not_null()
            )
            .sort("date")
            .group_by("symbol")
            .agg([pl.col("date").last(), pl.col("close").last()])
        )
        for row in recent.iter_rows(named=True):
            prices[row["symbol"]] = money.rupees(str(row["close"]))
            stale[row["symbol"]] = row["date"]

    return prices, price_date, stale
