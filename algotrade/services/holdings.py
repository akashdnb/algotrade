"""Holdings, rebuilt from execution history.

**Holdings are a derived view; executions are authoritative.** The `holdings`
collection is a materialised cache that exists so a daily valuation does not
have to replay years of fills. It is never edited in place -- it is rebuilt,
and `verify` re-derives it independently and reports any disagreement.

That distinction is what §9.1 requires: a reconstruction that reads the
snapshot cannot detect a corrupt snapshot.

FIFO lots come along for free, because replaying executions in order IS the
lot construction: a buy appends a lot, a sell consumes the oldest first (see
`domain.tax`). Realised P&L falls out of the same pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from algotrade.domain import money, tax
from algotrade.domain.models import Action, Execution, Holding, Lot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RebuildResult:
    holdings: list[Holding] = field(default_factory=list)
    realised_pnl: Decimal = money.ZERO
    executions_replayed: int = 0
    #: Sells that exceeded the position -- impossible in a consistent history,
    #: so they are surfaced rather than clamped.
    anomalies: list[str] = field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return sorted(h.symbol for h in self.holdings)


class HoldingsService:
    def __init__(self, *, executions, holdings, audit=None) -> None:
        self._executions = executions
        self._holdings = holdings
        self._audit = audit

    # ---------------------------------------------------------- derivation --

    def derive(
        self, executions: list[Execution], *, portfolio_id: str
    ) -> RebuildResult:
        """Replay executions into holdings. Pure -- touches no repository.

        Ordered by (executed_at, execution_id): a stable tiebreak matters
        because two fills can share a timestamp, and an unstable order would
        produce different lot ids run to run and break reproducibility.
        """
        result = RebuildResult()
        book: dict[str, Holding] = {}

        for execution in sorted(
            executions, key=lambda e: (e.executed_at, e.execution_id)
        ):
            result.executions_replayed += 1
            holding = book.setdefault(
                execution.symbol,
                Holding(portfolio_id=portfolio_id, symbol=execution.symbol,
                        isin=execution.isin),
            )

            if execution.action == Action.BUY:
                holding.lots.append(Lot(
                    # Derived from the execution, so a rebuild produces
                    # byte-identical lot ids every time.
                    lot_id=execution.execution_id,
                    acquired_on=execution.executed_at.date(),
                    quantity=execution.quantity,
                    price=execution.price,
                    charges=execution.charges,
                    source_set_id=execution.set_id,
                ))
                holding.updated_at = execution.executed_at
                continue

            try:
                sale = tax.sell_fifo(
                    holding,
                    quantity=execution.quantity,
                    price=execution.price,
                    on=execution.executed_at.date(),
                    charges=execution.charges,
                )
            except tax.InsufficientQuantity as exc:
                # A consistent history cannot produce this. Recording it and
                # continuing beats crashing a nightly job, but it MUST surface.
                result.anomalies.append(
                    f"{execution.execution_id}: {exc}"
                )
                continue

            holding.lots = list(sale.remaining_lots)
            holding.realised_pnl_ytd = money.rupees(
                holding.realised_pnl_ytd + sale.gain
            )
            holding.updated_at = execution.executed_at
            result.realised_pnl = money.rupees(result.realised_pnl + sale.gain)

        result.holdings = [h for h in book.values() if not h.is_empty]
        result.holdings.sort(key=lambda h: h.symbol)
        return result

    # ------------------------------------------------------------ rebuild ---

    def rebuild(self, portfolio_id: str, *, now: datetime) -> RebuildResult:
        """Recompute the materialised holdings from the full execution history.

        Idempotent by construction: it is a pure function of the executions, so
        running it twice produces the same book. Positions that no longer exist
        are deleted rather than left at zero -- a zero row must otherwise be
        filtered on every read, and the one place that forgets reports a closed
        position as held.
        """
        executions = self._all_executions(portfolio_id)
        result = self.derive(executions, portfolio_id=portfolio_id)

        derived = {h.symbol: h for h in result.holdings}
        existing = {h.symbol: h for h in self._holdings.book(portfolio_id)}

        for symbol, holding in derived.items():
            self._holdings.save(holding)
        for symbol in set(existing) - set(derived):
            self._holdings.save(
                Holding(portfolio_id=portfolio_id, symbol=symbol, lots=[])
            )

        if self._audit:
            self._audit.record(
                now=now, actor="system", action="holdings.rebuilt",
                entity_type="portfolio", entity_id=portfolio_id,
                portfolio_id=portfolio_id,
                after={
                    "positions": len(derived),
                    "executions": result.executions_replayed,
                    "realised_pnl": str(result.realised_pnl),
                },
                severity="warning" if result.anomalies else "info",
            )
        if result.anomalies:
            log.error("holdings rebuild anomalies for %s: %s",
                      portfolio_id, result.anomalies)
        return result

    # ------------------------------------------------------------- verify ---

    def verify(self, portfolio_id: str) -> list[str]:
        """Compare the stored book against one derived from executions.

        The independent check §9.1 asks for. If these disagree, the stored book
        is not what the trade record says it should be, and every valuation
        computed from it is wrong.
        """
        derived = {
            h.symbol: h
            for h in self.derive(
                self._all_executions(portfolio_id), portfolio_id=portfolio_id
            ).holdings
        }
        stored = {h.symbol: h for h in self._holdings.book(portfolio_id)}

        problems: list[str] = []
        for symbol in sorted(set(derived) | set(stored)):
            want, have = derived.get(symbol), stored.get(symbol)
            if want is None:
                problems.append(
                    f"{symbol}: stored {have.quantity} but executions imply none"
                )
            elif have is None:
                problems.append(
                    f"{symbol}: executions imply {want.quantity} but nothing is stored"
                )
            elif want.quantity != have.quantity:
                problems.append(
                    f"{symbol}: stored {have.quantity}, executions imply "
                    f"{want.quantity}"
                )
            elif abs(want.cost_basis - have.cost_basis) > Decimal("0.01"):
                problems.append(
                    f"{symbol}: stored basis {have.cost_basis}, executions imply "
                    f"{want.cost_basis}"
                )
        return problems

    def _all_executions(self, portfolio_id: str) -> list[Execution]:
        return self._executions.between(
            portfolio_id, start=date(1990, 1, 1), end=date(2100, 1, 1)
        )
