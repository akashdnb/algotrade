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
        previous_peak = self._snapshots.peak_unit_nav(portfolio_id) or money.ZERO
        peak = val.new_peak(unit_nav=unit_nav, peak=previous_peak)

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


def prices_as_of(
    panel, *, as_of: date, symbols: set[str] | None = None
) -> tuple[dict[str, Decimal], date]:
    """Closes from the latest session at or before `as_of`.

    Returns (prices, price_date). A weekend or holiday resolves to the previous
    trading day, which is correct rather than approximate -- no trading
    happened, so nothing moved.

    Raises:
        NoPricesAvailable: nothing on or before the date.
    """
    import polars as pl

    available = [d for d in panel["date"].unique().to_list() if d <= as_of]
    if not available:
        raise NoPricesAvailable(f"no session on or before {as_of}")
    price_date = max(available)

    day = panel.filter(pl.col("date") == price_date)
    if symbols is not None:
        day = day.filter(pl.col("symbol").is_in(list(symbols)))
    return (
        {
            r["symbol"]: money.rupees(str(r["close"]))
            for r in day.iter_rows(named=True)
            if r.get("close") is not None
        },
        price_date,
    )
