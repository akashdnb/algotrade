"""Share-count changes applied to a holding. Splits, bonuses, and their product.

A corporate action is the **second kind of event** in the append-only stream
that `executions` already forms. Holdings are a fold over both, in time order,
so a split survives the nightly `--rebuild-holdings` that would silently revert
any hand-patched lot (`services/holdings.py`).

**What scales, and what does not:**

    quantity      multiply by the ratio
    price         divide -- but derived from preserved cost, see `_rescale`
    charges       unchanged -- already paid, and a rupee is a rupee
    acquired_on   unchanged -- a split does not restart the holding period
    lot_id        unchanged -- the same acquisition, in more pieces

`acquired_on` is the one with teeth. Scaling is not a new acquisition, so the
long-term clock keeps running; resetting it would quietly reprice long-term
gains at the short-term rate.

**Only share-count multipliers belong here.** Dividends move cash, rights
depend on a subscription price *and* on whether the holder subscribed, and a
demerger hands you a different security. None of those is a ratio, so none is
representable as a `CorporateAction` -- they are refused upstream in
`services/corporate_actions.py` rather than guessed at.

Pure module: entities in, entities out. No I/O, no feed parsing, no clock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from fractions import Fraction

from algotrade.domain import money
from algotrade.domain.models import Holding, Lot

#: Post-split per-share cost is genuinely a repeating decimal sometimes
#: (1,507.50 over 7), and paise is not enough room to hold it: rounding there
#: drifts the cost basis past the 0.01 tolerance `HoldingsService.verify`
#: enforces. Four places keeps the drift per lot below a hundredth of a paisa.
PRICE_PLACES: Decimal = Decimal("0.0001")


class InapplicableAction(ValueError):
    """The action cannot be applied to this holding as stated.

    Raised rather than worked around: every case that reaches it is one where
    guessing produces a plausible wrong number, which is the failure mode this
    system is built to refuse.
    """


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One share-count change, already parsed and corroborated.

    `ratio` is the factor the share count multiplies by -- 2 for a 2:1 face
    value split, 4/3 for a bonus 1:3, and their product when both fall on one
    ex-date. It is a `Fraction` rather than a float because the fractional
    entitlement arithmetic below has to be exact: `floor` of a float that is
    5.999999999 instead of 6 silently destroys a share.

    `ex_date` is the boundary. Shares held at the close of the previous session
    are entitled; shares bought on the ex-date are already trading adjusted.
    """

    action_id: str
    symbol: str
    ex_date: date
    ratio: Fraction
    kind: str = ""
    subject: str = ""

    def __post_init__(self) -> None:
        if self.ratio <= 0:
            raise InapplicableAction(
                f"{self.action_id}: ratio must be > 0, got {self.ratio}"
            )
        # A ratio below 1 is a consolidation or reverse split -- the share count
        # FALLS. That needs a share-cancellation model this does not have (a lot
        # can round to zero shares while still holding cost), and `core.actions`
        # already refuses to parse those subjects. Refusing here too means the
        # two layers cannot disagree about what is supported.
        if self.ratio < 1:
            raise InapplicableAction(
                f"{self.action_id}: ratio {self.ratio} is below 1, which is a "
                f"consolidation or reverse split -- not supported, and never "
                f"applied silently"
            )

    @property
    def is_trivial(self) -> bool:
        """A ratio of exactly 1 changes nothing and is not worth recording."""
        return self.ratio == 1


def _allocate(quantities: list[int], total: int) -> list[int]:
    """Distribute `total` shares across lots by largest remainder.

    The registrar computes the entitlement on the **whole demat holding**, not
    per tax lot, and the two genuinely differ:

        lots of 2 and 2, bonus 1:3 (ratio 4/3)
          per lot:  floor(2 x 4/3) = 2, twice  -> 4 shares   wrong
          on total: floor(4 x 4/3) = 5         -> 5 shares   right

    So the new total is decided first and distributed here. Each lot takes its
    floor, then leftover shares go one at a time to the largest fractional
    remainders, ties broken by lot order -- which `Holding.lots` keeps in
    acquisition order, so the oldest lot wins a tie. Deterministic, because a
    rebuild has to reproduce it exactly.
    """
    held = sum(quantities)
    if held == 0:
        return [0] * len(quantities)

    # Exact rational share of the total, so the remainder comparison below is
    # not at the mercy of binary floating point.
    exact = [Fraction(q * total, held) for q in quantities]
    allocated = [int(e) for e in exact]  # Fraction -> int truncates toward zero

    leftover = total - sum(allocated)
    if leftover:
        order = sorted(
            range(len(quantities)),
            key=lambda i: (-(exact[i] - allocated[i]), i),
        )
        for i in order[:leftover]:
            allocated[i] += 1
    return allocated


def _rescale(lot: Lot, quantity: int) -> Lot:
    """The same lot holding `quantity` shares, for the same money.

    Price is derived from the lot's **preserved cost**, not by dividing by the
    ratio:

        new_price = (old_price x old_quantity) / new_quantity

    Those agree when the allocation came out exactly proportional and differ
    when the remainder had to land somewhere. Deriving from cost is the one
    that keeps every lot's basis right in both cases -- and the basis is what
    tax and P&L are computed from.

    Charges are left alone. They were paid in rupees on the original trade and
    no share-count change refunds any of them.
    """
    if quantity <= 0:
        raise InapplicableAction(
            f"lot {lot.lot_id}: would be left with {quantity} shares"
        )
    cost = lot.price * lot.quantity
    return replace(
        lot,
        quantity=quantity,
        price=(cost / quantity).quantize(PRICE_PLACES),
    )


@dataclass(frozen=True, slots=True)
class Applied:
    """What an action did to one position, for the audit trail."""

    action_id: str
    symbol: str
    ex_date: date
    ratio: Fraction
    quantity_before: int
    quantity_after: int
    cost_before: Decimal
    cost_after: Decimal
    #: Shares lost to rounding when the ratio did not divide the holding evenly.
    #: Real registrars aggregate these and pay cash in lieu, which is NOT
    #: modelled -- so this surfaces rather than being absorbed in silence.
    fractional_shares_dropped: Fraction = Fraction(0)

    @property
    def cost_preserved(self) -> bool:
        return abs(self.cost_after - self.cost_before) <= Decimal("0.01")


def apply_to_holding(
    holding: Holding, action: CorporateAction
) -> tuple[Holding, Applied]:
    """Scale a position for a share-count change. Returns the new book entry.

    The holding is rebuilt rather than mutated, so a caller that keeps the old
    one for comparison still has it.

    Raises:
        InapplicableAction: if the action names a different symbol, or the
            position is empty. Both mean the caller lined up the wrong two
            things, and scaling anyway would produce a number that looks fine.
    """
    if action.symbol != holding.symbol:
        raise InapplicableAction(
            f"{action.action_id} is for {action.symbol}, not {holding.symbol}"
        )
    if holding.is_empty:
        raise InapplicableAction(
            f"{action.action_id}: {holding.symbol} holds nothing on "
            f"{action.ex_date}"
        )

    before = holding.quantity
    # The entitlement is computed on the whole holding -- see `_allocate`.
    exact_total = before * action.ratio
    total = int(exact_total)
    allocated = _allocate([lot.quantity for lot in holding.lots], total)

    scaled = [
        _rescale(lot, quantity)
        for lot, quantity in zip(holding.lots, allocated)
    ]
    after = replace(holding, lots=scaled)

    return after, Applied(
        action_id=action.action_id,
        symbol=holding.symbol,
        ex_date=action.ex_date,
        ratio=action.ratio,
        quantity_before=before,
        quantity_after=after.quantity,
        cost_before=holding.cost_basis,
        cost_after=after.cost_basis,
        fractional_shares_dropped=exact_total - total,
    )


# --------------------------------------------------------------- spin-offs ---
#
# A demerger is NOT a ratio. The parent's share count does not change; you
# receive a *different security*, and part of your cost basis goes with it.
#
# **Discovery is manual, application is mechanical.** NSE's feed says the word
# "Demerger" and nothing else -- checked across every demerger that hit a
# liquid name 2019-2026, the subject is bare. Two numbers have to come from the
# company's own filing:
#
#   entitlement    child shares per parent share       (ITC Hotels: 1 per 10)
#   basis_fraction share of cost moving to the child   (s.49(2C)/(2D))
#
# Once a human supplies those, everything below is deterministic -- which is
# what lets a spin-off live in the same append-only stream as everything else
# and survive the nightly rebuild.


@dataclass(frozen=True, slots=True)
class Spinoff:
    """Shares in a new entity, carved out of an existing holding.

    `basis_fraction` is the share of cost that LEAVES the parent. The scheme
    sets it as (net book value of the demerged undertaking / net worth of the
    company immediately before the demerger), and the company publishes it --
    typically weeks after the ex-date, which is why this cannot be derived at
    the moment the price gaps.
    """

    action_id: str
    symbol: str                 # the parent; named `symbol` so the fold can
    child_symbol: str           # dispatch on it exactly as it does for a ratio
    ex_date: date
    entitlement: Fraction
    basis_fraction: Fraction
    subject: str = ""
    kind: str = "spinoff"

    def __post_init__(self) -> None:
        if self.entitlement <= 0:
            raise InapplicableAction(
                f"{self.action_id}: entitlement must be > 0"
            )
        if not (0 <= self.basis_fraction < 1):
            raise InapplicableAction(
                f"{self.action_id}: basis_fraction {self.basis_fraction} must "
                f"be in [0, 1) -- at 1 the parent would keep no cost at all"
            )
        if self.child_symbol == self.symbol:
            raise InapplicableAction(
                f"{self.action_id}: the child cannot be the parent"
            )


@dataclass(frozen=True, slots=True)
class SpunOff:
    """What a spin-off did, for the audit trail and the report."""

    action_id: str
    parent_symbol: str
    child_symbol: str
    ex_date: date
    parent_quantity: int
    child_quantity: int
    basis_moved: Decimal
    cost_before: Decimal
    cost_after: Decimal
    #: Entitlement that rounded away. Real registrars sell these and pay cash
    #: in lieu, which is NOT modelled -- see `apply_spinoff`.
    fractional_shares_dropped: Fraction = Fraction(0)

    @property
    def cost_preserved(self) -> bool:
        return abs(self.cost_after - self.cost_before) <= Decimal("0.01")


def apply_spinoff(
    holding: Holding, spinoff: Spinoff
) -> tuple[Holding, Holding | None, SpunOff]:
    """Carve a child position out of a parent. Returns (parent, child, record).

    Per lot: the parent keeps its quantity and gives up `basis_fraction` of its
    cost; the child receives that cost against the shares it actually
    generated, and **inherits `acquired_on`**. That inheritance is the point --
    s.2(42A)(g) carries the holding period to the child, so a long-term parent
    does not hand you a short-term child.

    Child quantity is decided on the **whole holding** and distributed by
    largest remainder, for the same reason bonuses are (`_allocate`): the
    registrar computes the entitlement on the demat position, not per tax lot.

    **Basis moves only where shares moved.** A lot whose entitlement rounds to
    zero keeps its full cost. That deviates from the scheme's strict
    arithmetic, which apportions the whole holding's basis -- but cash-in-lieu
    for the dropped fraction is not modelled, so moving basis with no asset
    behind it would quietly delete value from the book. Keeping the parent
    whole is the conservative direction, and the residue is reported.

    Returns `child=None` when the entitlement rounds away entirely -- a 1:10 on
    a 4-share position gives nothing but cash in lieu.
    """
    if spinoff.symbol != holding.symbol:
        raise InapplicableAction(
            f"{spinoff.action_id} is for {spinoff.symbol}, not {holding.symbol}"
        )
    if holding.is_empty:
        raise InapplicableAction(
            f"{spinoff.action_id}: {holding.symbol} holds nothing on "
            f"{spinoff.ex_date}"
        )

    parent_quantity = holding.quantity
    exact_child = parent_quantity * spinoff.entitlement
    child_total = int(exact_child)
    allocated = _allocate([lot.quantity for lot in holding.lots], child_total)

    parent_lots: list[Lot] = []
    child_lots: list[Lot] = []
    basis_moved = Decimal("0")

    for lot, child_quantity in zip(holding.lots, allocated):
        if child_quantity <= 0:
            parent_lots.append(lot)          # no shares moved, no cost moves
            continue

        cost = lot.price * lot.quantity
        # Decimal and Fraction do not multiply, and going via float would put
        # binary rounding into a cost basis. Numerator and denominator keep it
        # exact in Decimal arithmetic.
        moved = (cost * spinoff.basis_fraction.numerator
                 / spinoff.basis_fraction.denominator)
        basis_moved += moved

        parent_lots.append(replace(
            lot, price=((cost - moved) / lot.quantity).quantize(PRICE_PLACES)
        ))
        child_lots.append(Lot(
            # Derived from the parent lot, so a rebuild reproduces it exactly.
            lot_id=f"{lot.lot_id}~{spinoff.child_symbol}",
            acquired_on=lot.acquired_on,     # the holding period carries over
            quantity=child_quantity,
            price=(moved / child_quantity).quantize(PRICE_PLACES),
            # Charges stay wholly with the parent: they were paid to acquire
            # it, and no part of that brokerage bought the child.
            charges=money.ZERO,
            source_set_id=lot.source_set_id,
        ))

    parent = replace(holding, lots=parent_lots)
    child = (
        Holding(portfolio_id=holding.portfolio_id,
                symbol=spinoff.child_symbol, lots=child_lots)
        if child_lots else None
    )

    return parent, child, SpunOff(
        action_id=spinoff.action_id,
        parent_symbol=holding.symbol,
        child_symbol=spinoff.child_symbol,
        ex_date=spinoff.ex_date,
        parent_quantity=parent_quantity,
        child_quantity=child.quantity if child else 0,
        basis_moved=money.rupees(basis_moved),
        cost_before=holding.cost_basis,
        cost_after=money.rupees(
            parent.cost_basis + (child.cost_basis if child else money.ZERO)
        ),
        fractional_shares_dropped=exact_child - child_total,
    )


def ratio_from_factor(factor: float, *, max_denominator: int = 1000) -> Fraction:
    """The exact ratio behind a factor `core.actions` returned as a float.

    `core.actions` parses NSE subjects into floats, and this recovers the small
    rational that was meant: 2.0 is Fraction(2, 1), and 1.3333333333333333 is
    Fraction(4, 3) rather than a 53-bit approximation of it. Corporate-action
    ratios are always small rationals -- share counts are integers on both
    sides of the event -- so bounding the denominator is safe and turns float
    noise back into the intended number.

    Kept here rather than in `core.actions` because that module is a polars
    dependency the domain is forbidden to import, and this is the only piece of
    it the book needs.
    """
    return Fraction(factor).limit_denominator(max_denominator)
