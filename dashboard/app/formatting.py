"""Jinja filters. Money never becomes a float on the way to a template.

Indian digit grouping is not the Western one: 1,99,763 rather than 199,763.
Getting it wrong makes every figure on the page subtly foreign to read.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

RUPEE = "₹"


def _group_indian(digits: str) -> str:
    """Last three digits, then pairs: 12345678 -> 1,23,45,678."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def rupees(value: Decimal | int | None, *, paise: bool = False) -> str:
    """`₹1,99,763` — or `—` for absent, which is not the same as zero."""
    if value is None:
        return "—"
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return "—"

    quantum = Decimal("0.01") if paise else Decimal("1")
    amount = amount.quantize(quantum)
    negative = amount < 0
    amount = abs(amount)

    whole, _, frac = str(amount).partition(".")
    out = f"{RUPEE}{_group_indian(whole)}"
    if paise and frac:
        out += f".{frac}"
    return f"-{out}" if negative else out


def signed_rupees(value: Decimal | int | None, *, paise: bool = False) -> str:
    """Explicit sign, because a P&L of `₹695` reads as a balance, not a change."""
    if value is None:
        return "—"
    text = rupees(value, paise=paise)
    return text if text.startswith("-") else f"+{text}"


def pct(value: float | Decimal | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{places}f}%"


def signed_pct(value: float | Decimal | None, places: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:+.{places}f}%"


def day(value: date | datetime | str | None) -> str:
    """`28 Aug 2026`. Unambiguous, unlike any all-numeric format."""
    if value is None:
        return "—"
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {value:%b %Y}"


def ago(value: datetime | None, *, now: datetime | None = None) -> str:
    """Relative time. `3 days ago` answers "is this stale?" at a glance in a
    way an absolute timestamp does not."""
    if value is None:
        return "never"
    now = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)

    seconds = int((now - value).total_seconds())
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    days = hours // 24
    if days < 30:
        return f"{days} day{'s' if days > 1 else ''} ago"
    return day(value)


def qty(value: Decimal | int | float | None) -> str:
    if value is None:
        return "—"
    return _group_indian(str(int(value)))
