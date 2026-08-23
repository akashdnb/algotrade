"""Tests for the injectable clock."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from algotrade.platform import clock

D = date


def test_the_business_date_is_ist_not_utc():
    """20:00 IST on the 30th is 14:30 UTC the same day -- but 00:30 IST on the
    1st is 19:00 UTC on the PREVIOUS day. Deriving the business date from UTC
    puts every post-18:30-IST session on the wrong date."""
    c = clock.FixedClock(datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc))
    assert c.today() == D(2026, 10, 1)
    assert c.now().date() == D(2026, 9, 30)


def test_fixed_clock_refuses_a_naive_instant():
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.FixedClock(datetime(2026, 9, 30, 14, 30))


def test_system_clock_returns_an_aware_utc_instant():
    assert clock.SystemClock().now().tzinfo is timezone.utc


def test_weekends_are_identified():
    assert clock.is_weekend(D(2026, 10, 3))
    assert clock.is_weekend(D(2026, 10, 4))
    assert not clock.is_weekend(D(2026, 10, 2))


def test_previous_weekday_skips_the_weekend():
    assert clock.previous_weekday(D(2026, 10, 5)) == D(2026, 10, 2)


def test_weekdays_between_is_inclusive_and_excludes_weekends():
    assert clock.weekdays_between(D(2026, 10, 1), D(2026, 10, 7)) == [
        D(2026, 10, 1), D(2026, 10, 2), D(2026, 10, 5),
        D(2026, 10, 6), D(2026, 10, 7),
    ]


def test_period_of_formats_as_yyyy_mm():
    assert clock.period_of(D(2026, 9, 30)) == "2026-09"
    assert clock.period_of(D(2026, 12, 1)) == "2026-12"
