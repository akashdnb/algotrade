"""Diff the current book against a target book, producing ordered instructions.

Two ordering rules, both of which make the difference between an executable
plan and a list of wishes:

* **Sells before buys.** Indian equity settles T+1; proceeds from today's sale
  are not spendable today. A plan that lists buys first cannot be worked
  top-to-bottom, which is precisely how a human executes it.
* **Largest value first within each side.** A partial execution then leaves the
  book as close to target as the work done allows, and sells free the most cash
  soonest.

Pure module: no I/O, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from algotrade.domain import money
from algotrade.domain.construction import DEFAULT_MIN_TRADE_VALUE
from algotrade.domain.models import Action, Holding, Item


@dataclass(slots=True)
class Plan:
    """Ordered instructions plus what was deliberately not traded."""

    items: list[Item] = field(default_factory=list)
    #: symbol -> why no instruction was emitted. Distinguishes "nothing to do"
    #: from "something to do that we chose not to".
    skipped: dict[str, str] = field(default_factory=dict)

    @property
    def traded_value(self) -> Decimal:
        return money.sum_rupees(i.estimated_value for i in self.items)

    def turnover(self, *, book_value: Decimal) -> float:
        if book_value == 0:
            return 0.0
        return float(self.traded_value / book_value)


def _reason(symbol: str, action: Action, ranks: dict[str, int] | None,
            entry: int, exit_: int) -> str:
    """Why this trade exists, in the strategy's own terms.

    Stored on the item so a year later the audit trail answers "why was this
    bought?" without re-deriving a ranking against data that may since have
    been backfilled (§9.3).
    """
    if ranks is None or symbol not in ranks:
        return "target book differs from current holdings"
    rank = ranks[symbol]
    if action == Action.BUY:
        return f"rank {rank} <= entry {entry}"
    return f"rank {rank} > exit {exit_}"


def plan(
    *,
    holdings: list[Holding],
    target: dict[str, int],
    prices: dict[str, Decimal],
    min_trade_value: Decimal = DEFAULT_MIN_TRADE_VALUE,
    ranks: dict[str, int] | None = None,
    entry: int = 30,
    exit_: int = 60,
) -> Plan:
    """Instructions to move `holdings` to `target`.

    Args:
        holdings: Current positions. Only `symbol` and `quantity` are read.
        target: symbol -> desired share count (from `construction.build`).
        prices: symbol -> reference price. A **buy** without a price is
            refused; a **sell** without one is still emitted, because a
            delisted or suspended holding is exactly the position you need
            surfaced.
        min_trade_value: Deltas worth less than this are not traded, matching
            `construction` and `engine.run`.
        ranks: symbol -> momentum rank, used only to write the reason string.
    """
    result = Plan()
    current = {h.symbol: h.quantity for h in holdings if h.quantity > 0}

    sells: list[tuple[Decimal, str, int]] = []
    buys: list[tuple[Decimal, str, int]] = []

    for symbol in sorted(set(current) | set(target)):
        delta = target.get(symbol, 0) - current.get(symbol, 0)
        if delta == 0:
            continue

        price = prices.get(symbol)
        if price is None:
            if delta > 0:
                result.skipped[symbol] = "no price available; buy refused"
                continue
            # Selling something we cannot price: emit it anyway, at zero, so it
            # is visible. Ordering treats it as least valuable, which is right --
            # it frees no reliable cash.
            price = money.ZERO

        value = money.rupees(price * abs(delta))
        if price > 0 and value < min_trade_value:
            result.skipped[symbol] = (
                f"delta worth {value} below minimum trade value {min_trade_value}"
            )
            continue

        (buys if delta > 0 else sells).append((value, symbol, abs(delta)))

    # Largest first within each side; symbol breaks ties so the plan is stable
    # run to run and a re-generated set diffs cleanly against the previous one.
    sells.sort(key=lambda t: (-t[0], t[1]))
    buys.sort(key=lambda t: (-t[0], t[1]))

    seq = 1
    for action, group in ((Action.SELL, sells), (Action.BUY, buys)):
        for _value, symbol, quantity in group:
            result.items.append(
                Item(
                    seq=seq,
                    action=action,
                    symbol=symbol,
                    quantity=quantity,
                    reference_price=prices.get(symbol, money.ZERO),
                    reason=_reason(symbol, action, ranks, entry, exit_),
                )
            )
            seq += 1

    return result
