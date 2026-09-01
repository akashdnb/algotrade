"""The business date, in IST.

`date.today()` returns the *server's* date, and the deployment runs in UTC. IST
is UTC+5:30, so between 00:00 and 05:30 IST the server is still on yesterday --
and every "today" the dashboard computed was a day behind for that window.

That was not merely cosmetic. The record-fill form defaulted `executed_on` to
it, so a fill entered at 01:00 IST would have been dated to the previous
trading day and written into the book that way. The financial-year default did
the same on 1 April, sending the tax export to the wrong year.

The trading system already settled this. `algotrade.platform.clock` says it
outright:

    **IST, not UTC, decides the business date.** The exchange's day is an IST
    calendar day; deriving it from a UTC timestamp puts every session after
    18:30 IST on the wrong date.

So this delegates rather than re-deriving. One definition of "today" across the
jobs and the dashboard is the whole point of them sharing a repository.
"""

from __future__ import annotations

from datetime import date, datetime

from algotrade.platform import clock as _clock


def today() -> date:
    """The current business date in IST."""
    return _clock.SystemClock().today()


def now() -> datetime:
    """The current instant, timezone-aware, in UTC."""
    return _clock.SystemClock().now()


def session_date() -> date:
    """The trading session the current moment belongs to.

    Before NSE's publish hour, or at a weekend, that is the previous trading
    day -- the same rule the ingestion job uses to decide what it is fetching.
    """
    return _clock.session_date(now())
