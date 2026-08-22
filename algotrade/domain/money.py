"""Rupee arithmetic. Decimal only -- never float.

Two decisions worth stating, because both are silent-error sources:

**Floats are refused, not converted.** `rupees(100.5)` raises. By the time a
float reaches this function its error is already baked in, and accepting it
would launder that error into something that *looks* exact. The caller must
stringify at the point where the intended precision is known.

**Rounding is ROUND_HALF_UP, not Python's default ROUND_HALF_EVEN.** Banker's
rounding sends 2.005 to 2.00 and biases every rounded amount toward even paise.
Financial convention -- and every Indian broker contract note -- is half-up.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Iterable

#: Money is stored and computed to paise. Two places, always.
PAISE: Final[Decimal] = Decimal("0.01")

ZERO: Final[Decimal] = Decimal("0.00")


def rupees(value: str | int | Decimal | None) -> Decimal:
    """Coerce to a paise-quantized Decimal.

    Args:
        value: A string (preferred), int, Decimal, or None (-> zero).

    Raises:
        TypeError: for float, which cannot represent rupees exactly.
    """
    if value is None:
        return ZERO
    if isinstance(value, float):
        raise TypeError(
            "float is not accepted for money -- pass a str or Decimal so the "
            "intended precision is explicit (got "
            f"{value!r})"
        )
    if not isinstance(value, Decimal):
        value = Decimal(str(value))
    return value.quantize(PAISE, rounding=ROUND_HALF_UP)


def sum_rupees(values: Iterable[Decimal]) -> Decimal:
    """Exact sum. Returns zero for an empty iterable rather than raising."""
    total = ZERO
    for v in values:
        total += v
    return rupees(total)


def weight_of(value: Decimal, total: Decimal) -> float:
    """`value / total` as a float ratio, 0.0 when `total` is zero.

    A float is correct here: a weight is a ratio for display and comparison,
    never a quantity of money, and carrying Decimal through weight arithmetic
    implies a precision the number does not have.
    """
    if total == 0:
        return 0.0
    return float(value / total)


def percent_change(new: Decimal, old: Decimal) -> float:
    """Signed fractional change, 0.0 when `old` is zero."""
    if old == 0:
        return 0.0
    return float((new - old) / old)
