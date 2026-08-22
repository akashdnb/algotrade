"""The BSON boundary: Decimal <-> Decimal128, date <-> string, tz-aware instants.

Money changes representation exactly once, here. Three rules, each of which
exists because the alternative loses information silently:

* **Money is `Decimal128`, never `double`.** A float rupee amount is wrong the
  moment it is written, and reading one back is refused rather than laundered.
* **Business dates are strings.** A trading date is a calendar fact, not an
  instant. Stored as a BSON `Date` it acquires a midnight-UTC time, and IST is
  UTC+5:30 -- so any normalisation can move it a day. `"2026-09-30"` cannot.
* **Instants are timezone-aware, always UTC.** Writing a naive datetime is
  refused; pymongo returns naive UTC datetimes on read, so awareness is
  re-attached before anything above this layer sees them.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping

from bson.decimal128 import Decimal128

from algotrade.domain import money


# ------------------------------------------------------------------ money ---


def to_mongo_money(value: Decimal | None) -> Decimal128 | None:
    """Domain Decimal -> BSON Decimal128."""
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(
            f"refusing to store float {value!r} as money -- use Decimal (see domain.money)"
        )
    return Decimal128(money.rupees(value))


def from_mongo_money(value: Any) -> Decimal | None:
    """BSON Decimal128 (or int) -> domain Decimal.

    A `float` here means something bypassed `to_mongo_money`. Raising surfaces
    that at the read rather than letting the error propagate as if it were a
    real amount.
    """
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(
            f"money field holds a float ({value!r}); it was written outside this "
            f"codec and its precision cannot be trusted"
        )
    if isinstance(value, Decimal128):
        return money.rupees(value.to_decimal())
    if isinstance(value, (int, Decimal, str)):
        return money.rupees(value)
    raise TypeError(f"cannot read money from {type(value).__name__}")


# ------------------------------------------------------------------ dates ---


def to_mongo_date(value: date | None) -> str | None:
    """Business date -> ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        raise TypeError(
            "a datetime is an instant, not a business date -- pass .date() or "
            "use to_mongo_instant"
        )
    return value.isoformat()


def from_mongo_date(value: str | None) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(value)


# --------------------------------------------------------------- instants ---


def to_mongo_instant(value: datetime | None) -> datetime | None:
    """Timezone-aware datetime -> UTC datetime.

    A naive datetime is refused: assuming it means UTC is how a 5.5-hour error
    enters an audit log written from an IST machine.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError(
            f"naive datetime {value!r} has no timezone -- attach one explicitly "
            f"(platform.clock returns aware instants)"
        )
    return value.astimezone(timezone.utc)


def from_mongo_instant(value: datetime | None) -> datetime | None:
    """UTC datetime from the driver -> timezone-aware UTC datetime.

    pymongo returns naive datetimes representing UTC unless the client is
    configured otherwise; re-attaching tzinfo means nothing above this layer
    ever handles a naive instant.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


# ----------------------------------------------------------------- documents ---


def encode_doc(
    doc: Mapping[str, Any], *, money_fields: Iterable[str] = ()
) -> dict[str, Any]:
    """Copy `doc`, converting the named fields to Decimal128.

    A field absent from `doc` stays absent -- writing an explicit null would
    make "not applicable" indistinguishable from "zero" in the stored document.
    """
    out = dict(doc)
    for field in money_fields:
        if field in out:
            out[field] = to_mongo_money(out[field])
    return out


def decode_doc(
    doc: Mapping[str, Any], *, money_fields: Iterable[str] = ()
) -> dict[str, Any]:
    """Inverse of `encode_doc`."""
    out = dict(doc)
    for field in money_fields:
        if field in out:
            out[field] = from_mongo_money(out[field])
    return out
