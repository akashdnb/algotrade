"""NAV, unit accounting, drawdown, and return measurement.

**Unit accounting is why this module exists.** With ₹20k arriving every month,
raw NAV growth conflates "the strategy worked" with "I deposited money". A
contribution buys units at the prevailing unit NAV and therefore does not move
it, so `unit_nav` changes only with performance. That makes the time-weighted
series honest.

Both return measures are reported, because they answer different questions and
diverge materially for a monthly contributor (§11n):

* **TWR** (from `unit_nav`) -- how did the *strategy* do, ignoring when money
  arrived. The right number for comparing against an index.
* **XIRR** -- what did *you* actually earn, given when your money was at risk.
  The right number for the investor. §11n's decision turned on it.

Pure module: no I/O, no clock (dates are passed in).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Final, Sequence

from algotrade.domain import money
from algotrade.domain.models import Holding, Position

#: An arbitrary but conventional starting unit price. Only ratios matter.
BASE_UNIT_NAV: Final[Decimal] = Decimal("100.00")

#: Units carry three decimals: a ₹20k contribution at unit NAV ~₹120 buys
#: 166.667 units, and rounding that to whole units would leak real money.
_UNIT_QUANTUM: Final[Decimal] = Decimal("0.001")

DAYS_PER_YEAR: Final[float] = 365.25


def _units(value: Decimal) -> Decimal:
    from decimal import ROUND_HALF_UP

    return value.quantize(_UNIT_QUANTUM, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------- NAV -----


def value_positions(
    holdings: Sequence[Holding],
    prices: dict[str, Decimal],
    *,
    nav: Decimal | None = None,
) -> tuple[list[Position], Decimal]:
    """Mark holdings to `prices`.

    Args:
        holdings: Positions to value.
        prices: symbol -> close. A **missing price carries the position at
            zero and keeps it in the list** -- dropping it would understate the
            book and hide a suspension, which is exactly the event you need to
            see.
        nav: Denominator for weights. Defaults to invested value; pass the full
            NAV (invested + cash) so that weights reflect cash drag rather than
            summing to 1.0 regardless of how much is uninvested.

    Returns:
        (positions, invested_value).
    """
    priced: list[tuple[Holding, Decimal, Decimal]] = []
    for h in holdings:
        if h.quantity <= 0:
            continue
        close = prices.get(h.symbol, money.ZERO)
        priced.append((h, close, h.value_at(close)))

    invested = money.sum_rupees(v for _, _, v in priced)
    denominator = invested if nav is None else nav

    positions = [
        Position(
            symbol=h.symbol,
            quantity=h.quantity,
            close=close,
            value=value,
            weight=money.weight_of(value, denominator),
            unrealised_pnl=h.unrealised_pnl(close),
        )
        for h, close, value in priced
    ]
    positions.sort(key=lambda p: (-p.value, p.symbol))
    return positions, invested


# ----------------------------------------------------------------- units -----


def unit_nav(*, nav: Decimal, units: Decimal) -> Decimal:
    """NAV per unit. A portfolio with no units is at the base, not undefined."""
    if units <= 0:
        return BASE_UNIT_NAV
    return money.rupees(nav / units)


def apply_contribution(
    *, units: Decimal, nav_before: Decimal, contribution: Decimal
) -> tuple[Decimal, Decimal]:
    """Issue units for new money at the prevailing unit NAV.

    Returns:
        (units_after, unit_nav_used) -- the unit NAV is unchanged by the
        contribution, which is the invariant this function exists to preserve.
    """
    if contribution <= 0:
        return units, unit_nav(nav=nav_before, units=units)

    price = unit_nav(nav=nav_before, units=units)
    issued = contribution / price
    return _units(units + issued), price


# -------------------------------------------------------------- drawdown -----


def new_peak(*, unit_nav: Decimal, peak: Decimal) -> Decimal:
    """The running maximum. Ratchets up, never down."""
    return unit_nav if unit_nav > peak else peak


def drawdown(*, unit_nav: Decimal, peak: Decimal) -> float:
    """Fractional decline from the peak. Zero at or above it, never positive."""
    if peak <= 0:
        return 0.0
    if unit_nav >= peak:
        return 0.0
    return float((unit_nav - peak) / peak)


# --------------------------------------------------------------- returns -----


def twr(
    *, first_unit_nav: Decimal, last_unit_nav: Decimal, start: date, end: date
) -> float | None:
    """Annualised time-weighted return from the unit-NAV series.

    Annualised by **calendar span, not row count**. Row count annualises a
    250-session year as a 250-day year and flatters whichever series is
    sparser -- the defect §11j.6 found in `vs_benchmark_series`.

    Returns None when the span is zero or the starting NAV is not positive.
    """
    days = (end - start).days
    if days <= 0 or first_unit_nav <= 0:
        return None
    years = days / DAYS_PER_YEAR
    return float((last_unit_nav / first_unit_nav) ** Decimal(str(1 / years)) - 1)


def _npv(rate: float, flows: Sequence[tuple[date, Decimal]], t0: date) -> float:
    total = 0.0
    for when, amount in flows:
        years = (when - t0).days / DAYS_PER_YEAR
        total += float(amount) / (1.0 + rate) ** years
    return total


def xirr(
    flows: Sequence[tuple[date, Decimal]],
    *,
    tolerance: float = 1e-7,
    max_iterations: int = 200,
) -> float | None:
    """Money-weighted annual return for irregularly-timed cash flows.

    Sign convention: money **into** the portfolio is negative, the terminal
    value is positive.

    Solved by bisection rather than Newton-Raphson. Newton converges faster but
    diverges on the flow shapes this system actually produces (many same-signed
    contributions then one large terminal value), and a silently divergent
    return figure is worse than a slower correct one. Bisection on a bracketed
    sign change cannot diverge.

    Returns:
        The annual rate, or None when undefined -- fewer than two flows, or no
        sign change (nothing was ever actually invested, or nothing came back).
    """
    if len(flows) < 2:
        return None
    if not (any(a < 0 for _, a in flows) and any(a > 0 for _, a in flows)):
        return None

    ordered = sorted(flows, key=lambda f: f[0])
    t0 = ordered[0][0]
    if all(when == t0 for when, _ in ordered):
        return None

    # Bracket: -99.99% to +1000%. A rate outside that is not a real result.
    low, high = -0.9999, 10.0
    f_low, f_high = _npv(low, ordered, t0), _npv(high, ordered, t0)
    if f_low * f_high > 0:
        return None

    for _ in range(max_iterations):
        mid = (low + high) / 2
        f_mid = _npv(mid, ordered, t0)
        if abs(f_mid) < tolerance or (high - low) / 2 < tolerance:
            return mid
        if f_low * f_mid < 0:
            high, f_high = mid, f_mid
        else:
            low, f_low = mid, f_mid
    return (low + high) / 2
