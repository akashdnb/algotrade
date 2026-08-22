"""Fractional target weights -> integer share counts.

The backtest sizes positions as `weight x capital` and never has to buy a whole
number of anything. A real order does. At ₹2L a 1/30 position is ~₹6,700, and
the liquid-300 universe contains shares from ₹50 to ₹15,000 -- so a single
position can be 0.4 shares or 130 shares, and the rounding error is material.

Three rules, all of which exist because the alternative fails silently:

* **Below `min_trade_value`, do not trade.** The flat ₹15.34 DP charge makes a
  ₹300 trade cost 500+ bps. The engine already applies this (§ engine.run's
  `min_trade_value`); construction must agree or the plan will not match the
  backtest.
* **Never exceed the cash available.** A plan that overdraws is not a plan; it
  becomes a partial execution discovered at the broker.
* **Report what was dropped and why.** A name silently absent from the book is
  indistinguishable from a name the signal never selected.

Pure module: no I/O, no clock, no randomness.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Final

from algotrade.domain import money

#: Matches `backtest.engine.run(min_trade_value=500.0)`. Divergence here would
#: make the live plan differ from the validated one.
DEFAULT_MIN_TRADE_VALUE: Final[Decimal] = Decimal("500.00")


@dataclass(slots=True)
class ProposedBook:
    """Integer share counts and everything the caller needs to judge them."""

    quantities: dict[str, int] = field(default_factory=dict)
    deployed: Decimal = money.ZERO
    residual_cash: Decimal = money.ZERO
    investable: Decimal = money.ZERO
    #: symbol -> human-readable reason it is absent.
    dropped: dict[str, str] = field(default_factory=dict)

    @property
    def rounding_drag(self) -> float:
        """Fraction of investable capital left undeployed by integer rounding.

        Surfaced because a book that quietly deploys 94% of capital is not the
        fully-invested strategy that was validated.
        """
        if self.investable == 0:
            return 0.0
        return float(self.residual_cash / self.investable)


def reserve_for_charges(value: Decimal, *, bps: int) -> Decimal:
    """Hold back a proportional buffer so the last buy does not fail.

    Charges are not linear in trade size (the DP fee is flat per scrip), so this
    is an approximation on the conservative side -- it is a reserve, not an
    estimate, and being slightly too large costs only a little cash drag while
    being too small costs a failed order.
    """
    return money.rupees(value * Decimal(bps) / Decimal(10_000))


def build(
    targets: dict[str, float],
    *,
    investable: Decimal,
    prices: dict[str, Decimal],
    min_trade_value: Decimal = DEFAULT_MIN_TRADE_VALUE,
) -> ProposedBook:
    """Turn target weights into affordable integer share counts.

    Args:
        targets: symbol -> target weight. Used **as given**; a book summing to
            0.9 means 10% cash by intent and is not renormalised.
        investable: Rupees available to deploy. Pass this already net of any
            charge reserve (`reserve_for_charges`).
        prices: symbol -> price per share. A missing symbol is dropped, never
            guessed.
        min_trade_value: Positions worth less than this are dropped.

    Returns:
        A `ProposedBook`. `deployed + residual_cash == investable` always.
    """
    book = ProposedBook(investable=investable, residual_cash=investable)
    if investable <= 0 or not targets:
        book.residual_cash = max(investable, money.ZERO)
        return book

    # --- pass 1: floor each position, recording why any is excluded ----------
    remainders: dict[str, Decimal] = {}
    quantities: dict[str, int] = {}
    intended = money.ZERO

    for symbol, weight in targets.items():
        price = prices.get(symbol)
        if price is None:
            book.dropped[symbol] = "no price available for this session"
            continue
        if price <= 0:
            book.dropped[symbol] = f"non-positive price {price}"
            continue

        ideal_value = money.rupees(investable * Decimal(str(weight)))
        if ideal_value < min_trade_value:
            book.dropped[symbol] = (
                f"target {ideal_value} below minimum trade value {min_trade_value}"
            )
            continue
        if price > ideal_value:
            book.dropped[symbol] = (
                f"share price {price} exceeds the whole target position {ideal_value}"
            )
            continue

        exact = ideal_value / price
        whole = int(exact)  # truncates toward zero; exact is positive here
        quantities[symbol] = whole
        remainders[symbol] = exact - whole
        intended += ideal_value

    spent = money.sum_rupees(
        prices[s] * q for s, q in quantities.items() if q > 0
    )

    # --- pass 2: close the ROUNDING gap, largest fractional remainder first --
    # Bounded by what the targets actually asked for, NOT by all available cash.
    # Spending the full residual would silently renormalise a deliberately
    # under-invested book (targets summing to 0.9) up to fully invested,
    # overriding the caller's intent -- and any capital freed by a dropped name
    # would get scattered across the survivors as an unrequested overweight.
    #
    # Greedy rather than exact: with ~30 names and at most one position's worth
    # of cash in play, greedy and optimal almost always coincide, and a knapsack
    # would trade real complexity for imaginary precision.
    leftover = money.rupees(min(intended, investable) - spent)
    for symbol in sorted(remainders, key=lambda s: remainders[s], reverse=True):
        price = prices[symbol]
        if price <= leftover:
            quantities[symbol] += 1
            leftover = money.rupees(leftover - price)

    # A name that floored to zero and never got topped up was never bought.
    for symbol in [s for s, q in quantities.items() if q == 0]:
        del quantities[symbol]
        book.dropped.setdefault(
            symbol, "rounded down to zero shares and no cash left to top up"
        )

    book.quantities = quantities
    book.deployed = money.sum_rupees(prices[s] * q for s, q in quantities.items())
    book.residual_cash = money.rupees(investable - book.deployed)
    return book
