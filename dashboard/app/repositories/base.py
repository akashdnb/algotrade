"""Decoding at the boundary, so nothing downstream sees a BSON type.

Every money field is `Decimal128`. Handed to Jinja it renders as
`Decimal128("284310.22")`; converted to `float` it silently loses paise. It is
decoded exactly once, here -- the same rule the trading system follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable

from bson.decimal128 import Decimal128


def dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal128):
        return value.to_decimal()
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        # A float in a money field means something bypassed the trading
        # system's codec. Refuse it rather than rendering it as fact -- this is
        # the one place a corruption of that kind is still cheap to notice.
        raise TypeError(f"money field holds a float: {value!r}")
    return Decimal(str(value))


def day(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def instant(value: Any) -> datetime | None:
    """Naive datetimes out of Mongo are UTC; say so rather than assuming local."""
    if value is None:
        return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def decode(
    doc: dict,
    *,
    money: Iterable[str] = (),
    precise: Iterable[str] = (),
    dates: Iterable[str] = (),
    instants: Iterable[str] = (),
) -> dict:
    """`precise` is separate from `money` on purpose: `units` carries 3
    decimals and `unit_nav` 4, and quantizing them to paise was a real bug in
    the trading system once."""
    out = dict(doc)
    for field in (*money, *precise):
        if field in out:
            out[field] = dec(out[field])
    for field in dates:
        if field in out:
            out[field] = day(out[field])
    for field in instants:
        if field in out:
            out[field] = instant(out[field])
    return out


@dataclass(frozen=True, slots=True)
class Page:
    items: list
    total: int | None          # None when counting would cost more than it is worth
    limit: int
    offset: int

    @property
    def has_next(self) -> bool:
        if self.total is not None:
            return self.offset + self.limit < self.total
        return len(self.items) == self.limit

    @property
    def has_prev(self) -> bool:
        return self.offset > 0

    @property
    def page_number(self) -> int:
        return (self.offset // self.limit) + 1 if self.limit else 1

    @property
    def page_count(self) -> int | None:
        if self.total is None or not self.limit:
            return None
        return max(1, -(-self.total // self.limit))
