"""Rebalance decision: who enters, who leaves, how much of each, cash needed.

Pure decision logic. No broker, no orders, no persistence -- it takes the book
and a ranking and returns what the book should become.

**The rank buffer is the whole point and is easy to get wrong.** A held name is
NOT sold merely because it left the top 30; it is sold once it falls past rank
60. Refills come from the top of the ranking. Implementing this as "target =
top 30" would roughly double turnover and, at the flat DP charge, a large part
of the edge -- PROJECT.md §11j measured exactly that failure costing 2.6pp of
CAGR.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from algotrade.domain import construction, money, reconcile
from algotrade.domain.models import Holding, SignalSet

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RebalanceDecision:
    """What the book should become, and why."""

    as_of: date
    entering: list[str] = field(default_factory=list)
    leaving: list[str] = field(default_factory=list)
    holding: list[str] = field(default_factory=list)
    target_quantities: dict[str, int] = field(default_factory=dict)
    target_symbols: list[str] = field(default_factory=list)
    prices: dict[str, Decimal] = field(default_factory=dict)
    ranks: dict[str, int] = field(default_factory=dict)

    investable: Decimal = money.ZERO
    charge_reserve: Decimal = money.ZERO
    deployed: Decimal = money.ZERO
    residual_cash: Decimal = money.ZERO
    #: Cash needed BEYOND current cash, after sales settle. Negative = surplus.
    cash_required: Decimal = money.ZERO
    dropped: dict[str, str] = field(default_factory=dict)
    rounding_drag: float = 0.0

    @property
    def is_noop(self) -> bool:
        return not self.entering and not self.leaving


class RebalanceService:
    """Decides the target book. Recommends only -- never places an order."""

    def __init__(
        self, *, entry: int, exit_: int, charge_reserve_bps: int = 30,
        card=None,
    ) -> None:
        if exit_ < entry:
            raise ValueError(f"exit ({exit_}) must be >= entry ({entry})")
        self._entry = entry
        self._exit = exit_
        self._reserve_bps = charge_reserve_bps
        #: Zerodha rate card. Optional so the decision logic stays testable
        #: without config, but every production path supplies it -- the flat
        #: DP fee makes cost non-linear in trade size, so a bps estimate would
        #: understate a small trade badly (§11j).
        self._card = card

    # ------------------------------------------------------- membership ----

    def target_membership(
        self, *, held: list[str], signals: SignalSet
    ) -> tuple[list[str], list[str], list[str]]:
        """Apply the rank buffer. Returns (target, entering, leaving).

        Held names inside `exit` are kept in rank order; the book is then
        refilled from the top of the ranking up to `entry` names. A held name
        absent from the ranking entirely (delisted, or fallen out of the
        liquid universe) is treated as past the exit threshold.
        """
        ranks = {r.symbol: r.rank for r in signals.rows}
        MISSING = 10**9

        kept = sorted(
            (s for s in held if ranks.get(s, MISSING) <= self._exit),
            key=lambda s: ranks[s],
        )
        candidates = sorted(
            (s for s, r in ranks.items() if r <= self._entry and s not in kept),
            key=lambda s: ranks[s],
        )
        target = (kept + candidates)[: self._entry]

        entering = [s for s in target if s not in held]
        leaving = [s for s in held if s not in target]
        return target, entering, leaving

    # ------------------------------------------------------------ sizing ----

    def size(
        self,
        *,
        target: list[str],
        holdings: list[Holding],
        cash: Decimal,
        prices: dict[str, Decimal],
        allocated: Decimal | None = None,
    ) -> tuple[Decimal, Decimal, construction.ProposedBook]:
        """Equal-weight `target` into integer share counts at `prices`.

        Returns `(investable, charge_reserve, book)`.

        Extracted so `decide` (at the signal close) and `resize` (at the
        execution open) cannot drift apart. They differ **only** in the price
        vector and in whether membership is computed or given; the gross,
        reserve, weight and rounding arithmetic is one implementation, because
        two would eventually disagree and both would look authoritative.
        """
        # Everything currently held is liquid at these prices, so the book's
        # whole value is available to redeploy -- that is what a rebalance is.
        book_value = money.sum_rupees(
            h.value_at(prices.get(h.symbol, money.ZERO)) for h in holdings
        )
        gross = money.rupees(book_value + cash)

        # The cap, applied HERE and not in a screen. A UI-only limit is
        # decoration: it constrains what a human is shown, not what the
        # arithmetic produces, so the first defect that bypasses the screen
        # also bypasses the limit.
        #
        # It bounds the damage from ANY sizing defect to a known number
        # without needing to anticipate the defect -- the zero-priced-holdings
        # bug made 47,075 invisible to sizing, and a cap would have bounded
        # that class without knowing it existed.
        #
        # Applied as a ceiling, never a floor: a book worth less than its
        # allocation is simply worth less, and topping it up would deploy
        # money the ledger says is not there.
        if allocated is not None and gross > allocated:
            gross = money.rupees(allocated)

        reserve = construction.reserve_for_charges(gross, bps=self._reserve_bps)
        investable = money.rupees(gross - reserve)

        weights = {s: 1.0 / len(target) for s in target} if target else {}
        book = construction.build(weights, investable=investable, prices=prices)
        return investable, reserve, book

    #: Substring `construction.build` uses only for the "cannot afford even
    #: one share" drop (domain/construction.py). Matched narrowly on purpose:
    #: a missing or non-positive price is a data problem a substitute cannot
    #: fix, and surfacing it in `dropped` is how a feed defect gets noticed
    #: rather than quietly papered over.
    _UNAFFORDABLE = "exceeds the whole target position"

    def _fill_unaffordable_slots(
        self, *, target: list[str], held: list[str], holdings: list[Holding],
        cash: Decimal, prices: dict[str, Decimal], signals: SignalSet,
        investable: Decimal, reserve: Decimal, book: construction.ProposedBook,
    ) -> tuple[list[str], Decimal, Decimal, construction.ProposedBook]:
        """A newly-selected name too expensive for its equal-weight slot is
        swapped for the next-ranked candidate outside `target`, so the slot is
        deployed now rather than parked as cash until the next rebalance.

        Only a slot `entering` fresh is backfilled -- a HELD name the book
        sized to zero is being exited on purpose (it fell past the rank
        buffer, or no longer has a price), not declined for its price, and is
        not this function's business.

        Equal weight means every slot shares one budget, so a dropped slot's
        budget does not change as candidates are tried: this re-sizes after
        each swap only because `investable` itself can shift slightly (the
        charge reserve is a percentage of gross), not because the target
        slot's share of it does.
        """
        held_set = set(held)
        pool = sorted(
            (r.symbol for r in signals.rows if r.symbol not in target),
            key=lambda s: next(r.rank for r in signals.rows if r.symbol == s),
        )
        target = list(target)

        # Bounded by the ranking's own size: each pass consumes at least one
        # pool candidate, so this cannot outlive the universe it draws from.
        for _ in range(len(pool) + 1):
            unaffordable = [
                s for s in book.dropped
                if s not in held_set and self._UNAFFORDABLE in book.dropped[s]
            ]
            if not unaffordable or not pool:
                break
            for symbol in unaffordable:
                if not pool:
                    break
                target = [pool.pop(0) if s == symbol else s for s in target]
            investable, reserve, book = self.size(
                target=target, holdings=holdings, cash=cash, prices=prices)

        return target, investable, reserve, book

    def resize(
        self,
        *,
        target: list[str],
        holdings: list[Holding],
        cash: Decimal,
        prices: dict[str, Decimal],
        ranks: dict[str, int],
        as_of: date,
        allocated: Decimal | None = None,
    ) -> RebalanceDecision:
        """Re-derive quantities for an ALREADY DECIDED membership.

        This is design option B: the frozen artifact is the **weight**, and
        the quantity is a function of the execution price. `backtest.engine`
        computes weights at close(D) and fills at `open(D+1)`, so sizing on
        close(D) prices would apply the wrong price vector and make
        `construction.build`'s price argument decorative.

        **Membership is not recomputed.** The signal is frozen at close(D);
        only the prices move. Passing `target` in rather than deriving it from
        signals is what makes that structural instead of a convention.
        """
        held = [h.symbol for h in holdings if h.quantity > 0]
        investable, reserve, book = self.size(
            target=target, holdings=holdings, cash=cash, prices=prices,
            allocated=allocated)

        return RebalanceDecision(
            as_of=as_of,
            entering=[s for s in target if s not in held],
            leaving=[s for s in held if s not in target],
            holding=[s for s in target if s in held],
            target_quantities=book.quantities,
            target_symbols=list(target),
            prices=dict(prices),
            ranks=dict(ranks),
            investable=investable,
            charge_reserve=reserve,
            deployed=book.deployed,
            residual_cash=book.residual_cash,
            dropped=book.dropped,
            rounding_drag=book.rounding_drag,
        )

    def decide(
        self,
        *,
        holdings: list[Holding],
        signals: SignalSet,
        cash: Decimal,
        prices: dict[str, Decimal],
        as_of: date,
    ) -> RebalanceDecision:
        """The full decision: membership, integer sizing, and cash.

        Args:
            holdings: Current book.
            signals: Ranking as of `as_of`.
            cash: Uninvested rupees.
            prices: symbol -> price. Must cover held names too, or their
                value cannot be counted toward what is investable.
        """
        held = [h.symbol for h in holdings if h.quantity > 0]
        target, entering, leaving = self.target_membership(held=held, signals=signals)

        investable, reserve, book = self.size(
            target=target, holdings=holdings, cash=cash, prices=prices)
        target, investable, reserve, book = self._fill_unaffordable_slots(
            target=target, held=held, holdings=holdings, cash=cash,
            prices=prices, signals=signals,
            investable=investable, reserve=reserve, book=book)
        entering = [s for s in target if s not in held]
        leaving = [s for s in held if s not in target]

        # What the sells release, versus what the buys consume. Positive means
        # more cash is needed than the sales will produce -- which at T+1 is a
        # timing problem, not just an arithmetic one.
        buy_value = money.sum_rupees(
            prices[s] * max(book.quantities.get(s, 0) - _qty(holdings, s), 0)
            for s in book.quantities
            if s in prices
        )
        sell_value = money.sum_rupees(
            prices.get(s, money.ZERO) * max(_qty(holdings, s) - book.quantities.get(s, 0), 0)
            for s in {h.symbol for h in holdings} | set(book.quantities)
        )

        return RebalanceDecision(
            as_of=as_of,
            entering=entering,
            leaving=leaving,
            holding=[s for s in target if s in held],
            target_quantities=book.quantities,
            target_symbols=target,
            prices=dict(prices),
            ranks={r.symbol: r.rank for r in signals.rows},
            investable=investable,
            charge_reserve=reserve,
            deployed=book.deployed,
            residual_cash=book.residual_cash,
            cash_required=money.rupees(buy_value - sell_value - cash),
            dropped=book.dropped,
            rounding_drag=book.rounding_drag,
        )

    # ------------------------------------------------------------- trades ---

    def plan(
        self, *, holdings: list[Holding], decision: RebalanceDecision,
        min_trade_value: Decimal | None = None,
    ) -> reconcile.Plan:
        """Ordered instructions. Sells first -- T+1 funds the buys."""
        kwargs = {}
        if min_trade_value is not None:
            kwargs["min_trade_value"] = min_trade_value
        if self._card is not None:
            from algotrade.core.costs import order_charges

            # `order_charges` works in float; money.rupees REFUSES floats, so
            # the conversion is made explicit at exactly two places rather than
            # laundered. That guard is why this failed loudly instead of
            # storing a subtly wrong charge.
            kwargs["charge_for"] = lambda value, action: money.rupees(
                f"{order_charges(float(value), action.value, self._card).total:.2f}"
            )
        return reconcile.plan(
            holdings=holdings,
            target=decision.target_quantities,
            prices=decision.prices,
            ranks=decision.ranks,
            entry=self._entry,
            exit_=self._exit,
            **kwargs,
        )


def _qty(holdings: list[Holding], symbol: str) -> int:
    for h in holdings:
        if h.symbol == symbol:
            return h.quantity
    return 0
