"""The fixed basket you approve before touching a broker.

Assembles one screen's worth of truth from a pending recommendation:

    frozen membership -> execution-day opens -> resized quantities
                      -> sell-first instructions -> costs

**Membership comes from the recommendation, never from a fresh signal.** The
target book is `holds` plus everything the set says to buy. Recomputing it here
would silently run a different strategy than the one that was approved, on a
different session's ranking.

**The quantities are recomputed, and that is the point.** The recommendation
sized at close(D); the engine fills at `open(D+1)`. Re-sizing at the open is
what makes the live book match the backtested construction (design §6).

**Nothing here is a share count you cannot afford to look at.** The drift
against the recommendation is computed and shown rather than hidden, because a
quantity that changed between approval and execution is exactly the thing a
reviewer needs to see before clicking anything.

Pure: no repository, no network, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Item,
    RecommendationSet,
    SetStatus,
)

#: Set states that still describe work someone might do.
#:
#: The exclusions are the point. A **superseded** set has been replaced by a
#: later revision and its items stay `pending` forever -- nothing marks them
#: executed, because they never will be. Selecting on open items alone
#: therefore resurrects a dead set: on 2026-09-05 the review page walked past
#: a `completed` r3 and assembled a basket from the `superseded` r2 behind it.
#:
#: `expired` is a signal gone stale, `rejected` is a deliberate human no, and
#: `completed` is finished. None of them is a basket to trade.
ACTIONABLE: frozenset[SetStatus] = frozenset({
    SetStatus.PENDING,
    SetStatus.NOTIFIED,
    SetStatus.APPROVED,
    SetStatus.PARTIALLY_EXECUTED,
})


def is_actionable(recommendation: RecommendationSet) -> bool:
    """Whether this set is one you could still execute.

    Both conditions, not either: a live status **and** something left to do.
    """
    return (recommendation.status in ACTIONABLE
            and bool(recommendation.open_items))


def target_membership(recommendation: RecommendationSet) -> list[str]:
    """The book the recommendation intends: retained names plus new buys.

    `holds` is deliberately not stored as items -- a zero-delta HOLD would be
    counted in turnover and shown as outstanding work -- so the target has to
    be reassembled from both, and neither half alone is the book.
    """
    buys = [i.symbol for i in recommendation.items if i.action == Action.BUY]
    seen: set[str] = set()
    out: list[str] = []
    for symbol in list(recommendation.holds) + buys:
        if symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


@dataclass(frozen=True, slots=True)
class Line:
    """One instruction as the reviewer sees it, beside what was approved."""

    seq: int
    action: str
    symbol: str
    quantity: int
    price: Decimal
    charges: Decimal
    reason: str
    #: The quantity the recommendation carried for this symbol and side, if
    #: any. `None` means the instruction is new since approval.
    approved_quantity: int | None = None

    @property
    def value(self) -> Decimal:
        return money.rupees(self.price * self.quantity)

    @property
    def drift(self) -> int | None:
        if self.approved_quantity is None:
            return None
        return self.quantity - self.approved_quantity

    @property
    def is_new(self) -> bool:
        return self.approved_quantity is None


@dataclass
class Review:
    """Everything the screen shows, decided before it renders."""

    set_id: str
    lines: list[Line] = field(default_factory=list)
    #: Symbols that could not be priced, so cannot be sized. Blocking.
    unpriced: list[str] = field(default_factory=list)
    #: Symbols `construction.build` refused, with the reason.
    dropped: dict[str, str] = field(default_factory=dict)
    #: Symbols with a target but no instruction, and why.
    skipped: dict[str, str] = field(default_factory=dict)
    investable: Decimal = money.ZERO
    charge_reserve: Decimal = money.ZERO
    deployed: Decimal = money.ZERO
    residual_cash: Decimal = money.ZERO
    #: How many names the recommendation intends to hold. Carried so a
    #: blocked basket can say "none of 30" rather than a bare count, which
    #: reads very differently.
    target_size: int = 0

    @property
    def sells(self) -> list[Line]:
        return [l for l in self.lines if l.action == Action.SELL]

    @property
    def buys(self) -> list[Line]:
        return [l for l in self.lines if l.action == Action.BUY]

    @property
    def estimated_charges(self) -> Decimal:
        return money.sum_rupees(l.charges for l in self.lines)

    @property
    def sell_value(self) -> Decimal:
        return money.sum_rupees(l.value for l in self.sells)

    @property
    def buy_value(self) -> Decimal:
        return money.sum_rupees(l.value for l in self.buys)

    @property
    def drifted(self) -> list[Line]:
        """Instructions whose size moved since approval, worth reading."""
        return [l for l in self.lines if l.drift]

    @property
    def ready(self) -> bool:
        """Whether this basket can be executed as shown.

        An unpriced target blocks it. Sizing around a name we could not price
        would quietly produce a 29-stock book from a 30-stock decision, and
        the screen would look entirely normal.
        """
        return not self.unpriced


def blocked(
    recommendation: RecommendationSet,
    *,
    unpriced: list[str],
) -> Review:
    """A basket that cannot be priced, carrying **no instructions at all**.

    Not "the instructions, at zero". Sizing against a partial price vector
    empties the target book, so every holding falls out of it and the plan
    becomes a sell of the entire portfolio at a reference price of zero. On the
    one screen read immediately before trading, that renders as a full
    liquidation — the most alarming thing it could possibly say, and untrue.

    The distinction matters most on a day the market never opened: a Saturday
    produced exactly that page.
    """
    return Review(set_id=recommendation.set_id, unpriced=sorted(unpriced),
                  target_size=len(target_membership(recommendation)))


def _approved(recommendation: RecommendationSet) -> dict[tuple[str, str], int]:
    return {(i.action, i.symbol): i.quantity for i in recommendation.items}


def build(
    *,
    recommendation: RecommendationSet,
    items: list[Item],
    unpriced: list[str],
    dropped: dict[str, str],
    skipped: dict[str, str],
    investable: Decimal,
    charge_reserve: Decimal,
    deployed: Decimal,
    residual_cash: Decimal,
) -> Review:
    """Turn resized instructions into the screen, keeping the approved sizes.

    `items` are the freshly planned instructions -- already sells-first, since
    `reconcile.plan` orders them that way and T+1 requires it.
    """
    approved = _approved(recommendation)
    return Review(
        set_id=recommendation.set_id,
        target_size=len(target_membership(recommendation)),
        lines=[
            Line(
                seq=item.seq,
                action=item.action,
                symbol=item.symbol,
                quantity=item.quantity,
                price=item.reference_price,
                charges=item.estimated_charges,
                reason=item.reason,
                approved_quantity=approved.get((item.action, item.symbol)),
            )
            for item in items
        ],
        unpriced=sorted(unpriced),
        dropped=dict(dropped),
        skipped=dict(skipped),
        investable=investable,
        charge_reserve=charge_reserve,
        deployed=deployed,
        residual_cash=residual_cash,
    )
