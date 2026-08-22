"""Time, injected rather than read from the global clock.

Every job is driven by "what business date is this?" and every document carries
timestamps. Reading `datetime.now()` directly makes all of that untestable and
makes a run at 00:15 IST behave differently from one at 23:45 -- so the clock
is a dependency like any other.

**IST, not UTC, decides the business date.** The exchange's day is an IST
calendar day; deriving it from a UTC timestamp puts every session after 18:30
IST on the wrong date.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Final, Protocol

#: India Standard Time. Fixed offset -- India observes no DST, so a full
#: timezone database is unnecessary and one less dependency to keep current.
IST: Final[timezone] = timezone(timedelta(hours=5, minutes=30))


class Clock(Protocol):
    def now(self) -> datetime:
        """The current instant, timezone-aware, in UTC."""

    def today(self) -> date:
        """The current business date in IST."""


class SystemClock:
    """The real clock."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def today(self) -> date:
        return datetime.now(IST).date()


class FixedClock:
    """A clock pinned to one instant, for tests and for `--date` overrides."""

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("FixedClock needs a timezone-aware instant")
        self._instant = instant.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._instant

    def today(self) -> date:
        return self._instant.astimezone(IST).date()


def is_weekend(day: date) -> bool:
    """NSE trades Monday to Friday.

    Holidays are NOT handled here on purpose: no trustworthy calendar exists in
    this stack (§10.9 -- `nsefin` returned False for Independence Day, silently).
    A holiday is detected from NSE's archive returning 404, which is a fact
    rather than a prediction.
    """
    return day.weekday() >= 5


def previous_weekday(day: date) -> date:
    d = day - timedelta(days=1)
    while is_weekend(d):
        d -= timedelta(days=1)
    return d


def weekdays_between(start: date, end: date) -> list[date]:
    """Weekdays in [start, end], inclusive. The candidate-session list."""
    out, d = [], start
    while d <= end:
        if not is_weekend(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def period_of(day: date) -> str:
    """The rebalance period a date belongs to, as `YYYY-MM`."""
    return f"{day.year:04d}-{day.month:02d}"
