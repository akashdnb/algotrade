"""Holdings, rebuilt from execution and corporate-action history.

**Holdings are a derived view; the event stream is authoritative.** The
`holdings` collection is a materialised cache that exists so a daily valuation
does not have to replay years of fills. It is never edited in place -- it is
rebuilt, and `verify` re-derives it independently and reports any disagreement.

That distinction is what §9.1 requires: a reconstruction that reads the
snapshot cannot detect a corrupt snapshot.

FIFO lots come along for free, because replaying executions in order IS the
lot construction: a buy appends a lot, a sell consumes the oldest first (see
`domain.tax`). Realised P&L falls out of the same pass.

**Two kinds of event, one stream.** Corporate actions replay alongside
executions because a split is not something you can patch into the cache: this
rebuild runs nightly (`daily.yml` passes `--rebuild-holdings`), so a hand-edited
lot is reverted within a day and `verify` flags the patched book as disagreeing
with its own trade record. Making the split an *event* is what lets the fold
stay a pure function of stored data.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from algotrade.domain import corporate_actions as ca
from algotrade.domain import money, tax
from algotrade.domain.models import Action, Execution, Holding, Lot

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RebuildResult:
    holdings: list[Holding] = field(default_factory=list)
    realised_pnl: Decimal = money.ZERO
    executions_replayed: int = 0
    actions_replayed: int = 0
    #: Sells that exceeded the position -- impossible in a consistent history,
    #: so they are surfaced rather than clamped.
    anomalies: list[str] = field(default_factory=list)
    #: What each corporate action did, for the audit trail and the report.
    applied_actions: list[ca.Applied] = field(default_factory=list)
    #: Demergers, kept separate because they create a position rather than
    #: resize one -- a reader scanning for "what appeared in my book" needs
    #: these, and they do not fit the ratio shape above.
    spinoffs: list[ca.SpunOff] = field(default_factory=list)

    @property
    def symbols(self) -> list[str]:
        return sorted(h.symbol for h in self.holdings)


class HoldingsService:
    def __init__(
        self, *, executions, holdings, audit=None, corporate_actions=None
    ) -> None:
        self._executions = executions
        self._holdings = holdings
        self._audit = audit
        # Optional so every existing call site keeps working. A book with no
        # action history derives exactly as it did before -- which is also what
        # makes this safe to land on a live database.
        self._corporate_actions = corporate_actions

    # ---------------------------------------------------------- derivation --

    def derive(
        self,
        executions: list[Execution],
        *,
        portfolio_id: str,
        actions: list[ca.CorporateAction] | None = None,
    ) -> RebuildResult:
        """Replay the event stream into holdings. Pure -- touches no repository.

        Executions are ordered by (executed_at, execution_id): a stable
        tiebreak matters because two fills can share a timestamp, and an
        unstable order would produce different lot ids run to run and break
        reproducibility.

        **A corporate action sorts before every execution on its ex-date**,
        which is precisely what an ex-date means:

        * shares held at the close of the previous session are entitled, so
          they scale
        * shares bought on the ex-date are bought at the already-adjusted
          price, so they must not

        Ordering alone settles that. A lot acquired on the ex-date is appended
        *after* the action has fired, so it is never scaled -- no date filter
        inside the fold, and no special case anyone has to remember. That is
        also why the key uses the execution's business date rather than its
        instant: comparing a date against a UTC timestamp is where a
        timezone-shifted fill would land on the wrong side of the boundary.
        """
        result = RebuildResult()
        book: dict[str, Holding] = {}

        for event in self._ordered(executions, actions or []):
            if isinstance(event, ca.Spinoff):
                self._apply_spinoff(event, book, result)
                continue
            if isinstance(event, ca.CorporateAction):
                self._apply_action(event, book, result)
                continue

            execution = event
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
            holding.realised_pnl = money.rupees(
                holding.realised_pnl + sale.gain
            )
            holding.updated_at = execution.executed_at
            result.realised_pnl = money.rupees(result.realised_pnl + sale.gain)

        result.holdings = [h for h in book.values() if not h.is_empty]
        result.holdings.sort(key=lambda h: h.symbol)
        return result

    @staticmethod
    def _ordered(
        executions: list[Execution], actions: list[ca.CorporateAction]
    ) -> list:
        """The merged event stream, in the order the book experienced it.

        Sorted by (business date, kind, tiebreak) where an action's kind sorts
        first. See `derive` for why that ordering is the whole ex-date rule.
        """
        stream: list[tuple[date, int, str, object]] = [
            (e.executed_at.date(), 1, e.execution_id, e) for e in executions
        ]
        stream += [(a.ex_date, 0, a.action_id, a) for a in actions]
        stream.sort(key=lambda row: (row[0], row[1], row[2]))
        return [row[3] for row in stream]

    @staticmethod
    def _apply_action(
        action: ca.CorporateAction,
        book: dict[str, Holding],
        result: RebuildResult,
    ) -> None:
        """Scale one position, or record why it could not be scaled.

        An action for a symbol not held is silently ignored rather than
        treated as an error: discovery records the action per portfolio at the
        time it applied, and a later sell legitimately leaves the book with
        nothing to scale when the history is replayed from the start.
        """
        holding = book.get(action.symbol)
        if holding is None or holding.is_empty:
            return

        try:
            scaled, applied = ca.apply_to_holding(holding, action)
        except ca.InapplicableAction as exc:
            # Must surface. An unapplied action means the book is valuing a
            # post-split quantity at a pre-split cost, which is exactly the
            # class of plausible wrong number this system exists to refuse.
            result.anomalies.append(f"{action.action_id}: {exc}")
            return

        book[action.symbol] = scaled
        result.actions_replayed += 1
        result.applied_actions.append(applied)

    @staticmethod
    def _apply_spinoff(
        spinoff: ca.Spinoff,
        book: dict[str, Holding],
        result: RebuildResult,
    ) -> None:
        """Carve a child position out of a parent, in place in the book.

        The child is created by this replay rather than stored, so it is a pure
        function of the event like everything else here -- which is what makes
        `verify` able to re-derive it independently.
        """
        parent = book.get(spinoff.symbol)
        if parent is None or parent.is_empty:
            return

        try:
            remaining, child, record = ca.apply_spinoff(parent, spinoff)
        except ca.InapplicableAction as exc:
            result.anomalies.append(f"{spinoff.action_id}: {exc}")
            return

        book[spinoff.symbol] = remaining
        if child is not None:
            # A position in the child may already exist -- it can be bought
            # like any other name once it lists. Merging keeps ONE holding per
            # symbol, which `HoldingRepository` requires: its _id is
            # portfolio:symbol, so two would silently overwrite each other.
            existing = book.get(spinoff.child_symbol)
            if existing is not None and not existing.is_empty:
                existing.lots.extend(child.lots)
                existing.lots.sort(key=lambda l: (l.acquired_on, l.lot_id))
            else:
                book[spinoff.child_symbol] = child

        result.actions_replayed += 1
        result.spinoffs.append(record)

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
        result = self.derive(
            executions,
            portfolio_id=portfolio_id,
            actions=self._all_actions(portfolio_id),
        )

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
                self._all_executions(portfolio_id),
                portfolio_id=portfolio_id,
                actions=self._all_actions(portfolio_id),
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

    def _all_actions(self, portfolio_id: str) -> list[ca.CorporateAction]:
        """Applied corporate actions, or none when the repository is absent.

        `verify` and `rebuild` MUST read from the same source here. If only one
        of them replayed actions, a correctly split-adjusted book would be
        reported as disagreeing with its own trade record every night.
        """
        if self._corporate_actions is None:
            return []
        return self._corporate_actions.applied(portfolio_id)
