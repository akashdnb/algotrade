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
from decimal import ROUND_HALF_UP, Decimal
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


# -------------------------------------------------- non-money decimals ------
#
# Not every Decimal is rupees. `units` carries THREE decimals and `unit_nav`
# FOUR, because unit accounting exists so that a contribution does not move the
# unit NAV -- and quantizing units to paise reintroduces exactly the drift the
# mechanism is there to prevent. A 20k contribution at unit NAV ~120 buys
# 166.667 units; storing 166.67 leaks value on every single contribution.


def to_mongo_decimal(value: Decimal | None, *, places: int) -> Decimal128 | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(f"refusing to store float {value!r} as a decimal")
    quantum = Decimal(1).scaleb(-places)
    return Decimal128(Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP))


def from_mongo_decimal(value: Any, *, places: int) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, float):
        raise TypeError(f"decimal field holds a float ({value!r})")
    raw = value.to_decimal() if isinstance(value, Decimal128) else Decimal(str(value))
    quantum = Decimal(1).scaleb(-places)
    return raw.quantize(quantum, rounding=ROUND_HALF_UP)


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


# ------------------------------------------------------------ nested money ---
#
# Money nested inside a sub-document or an array of sub-documents -- a
# portfolio's benchmark sleeve, a holding's lots, a snapshot's positions. Hand-
# rolling this per repository is how one gets missed, so it is declared as data
# (see `schema.NESTED_MONEY_FIELDS`) and applied uniformly.


def _walk_nested(
    doc: dict[str, Any], spec: Mapping[str, Iterable[str]], convert
) -> dict[str, Any]:
    """Apply `convert` to the named fields inside each declared sub-document.

    A path ending in `[]` denotes an array of sub-documents; otherwise a single
    optional sub-document.
    """
    for path, fields in spec.items():
        is_array = path.endswith("[]")
        key = path[:-2] if is_array else path
        value = doc.get(key)
        if value is None:
            continue

        children = value if is_array else [value]
        converted = []
        for child in children:
            if not isinstance(child, Mapping):
                converted.append(child)
                continue
            child = dict(child)
            for field in fields:
                if field in child:
                    child[field] = convert(child[field])
            converted.append(child)
        doc[key] = converted if is_array else converted[0]
    return doc


def encode_nested_precise(
    doc: Mapping[str, Any], spec: Mapping[str, Mapping[str, int]]
) -> dict[str, Any]:
    """Non-money decimals inside sub-documents, at their declared precision.

    The nested counterpart of `encode_precise`, and it exists for the same
    reason: a corporate action divides a lot's price by the share-count ratio,
    and a per-share cost after a 1:3 bonus does not land on a paisa. Rounding
    it there drifts the lot's cost basis away from what was actually paid --
    past the 0.01 tolerance `HoldingsService.verify` enforces, which would make
    a correctly-derived book look like a corrupt one.
    """
    return _walk_nested_typed(dict(doc), spec, to_mongo_decimal)


def decode_nested_precise(
    doc: Mapping[str, Any], spec: Mapping[str, Mapping[str, int]]
) -> dict[str, Any]:
    return _walk_nested_typed(dict(doc), spec, from_mongo_decimal)


def _walk_nested_typed(
    doc: dict[str, Any], spec: Mapping[str, Mapping[str, int]], convert
) -> dict[str, Any]:
    """`_walk_nested`, but each field carries its own `places` argument."""
    for path, fields in spec.items():
        is_array = path.endswith("[]")
        key = path[:-2] if is_array else path
        value = doc.get(key)
        if value is None:
            continue

        children = value if is_array else [value]
        converted = []
        for child in children:
            if not isinstance(child, Mapping):
                converted.append(child)
                continue
            child = dict(child)
            for field, places in fields.items():
                if field in child:
                    child[field] = convert(child[field], places=places)
            converted.append(child)
        doc[key] = converted if is_array else converted[0]
    return doc


def encode_precise(
    doc: Mapping[str, Any], spec: Mapping[str, int]
) -> dict[str, Any]:
    """Encode non-money decimals at their declared precision."""
    out = dict(doc)
    for field, places in spec.items():
        if field in out:
            out[field] = to_mongo_decimal(out[field], places=places)
    return out


def decode_precise(
    doc: Mapping[str, Any], spec: Mapping[str, int]
) -> dict[str, Any]:
    out = dict(doc)
    for field, places in spec.items():
        if field in out:
            out[field] = from_mongo_decimal(out[field], places=places)
    return out


def encode_nested(
    doc: Mapping[str, Any], spec: Mapping[str, Iterable[str]]
) -> dict[str, Any]:
    return _walk_nested(dict(doc), spec, to_mongo_money)


def decode_nested(
    doc: Mapping[str, Any], spec: Mapping[str, Iterable[str]]
) -> dict[str, Any]:
    return _walk_nested(dict(doc), spec, from_mongo_money)
