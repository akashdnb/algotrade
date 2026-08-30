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


# --------------------------------------------------------- session_date -----
#
# These pin the two failures actually observed in production on 2026-08-27/28,
# when GitHub delayed the daily run past IST midnight.


def test_a_run_delayed_past_ist_midnight_still_belongs_to_the_prior_session():
    """The real incident: scheduled 14:30 UTC Thu 27 Aug, fired 23:51 UTC --
    05:21 IST on Fri 28th. `today()` says the 28th, but the session that had
    just been published, and that the run actually ingested, was the 27th."""
    late = datetime(2026, 8, 27, 23, 51, tzinfo=timezone.utc)
    assert clock.SystemClock  # module sanity
    assert late.astimezone(clock.IST).date() == D(2026, 8, 28)   # what it WAS
    assert clock.session_date(late) == D(2026, 8, 27)            # what it IS


def test_a_run_delayed_into_saturday_morning_belongs_to_friday():
    """The second incident, worse: 23:37 UTC Fri 28th = 05:06 IST Sat 29th.
    The old rule recorded a job_run against a Saturday -- a day the exchange
    was shut and no session can ever exist for."""
    late = datetime(2026, 8, 28, 23, 37, tzinfo=timezone.utc)
    assert late.astimezone(clock.IST).date() == D(2026, 8, 29)   # a Saturday
    assert clock.session_date(late) == D(2026, 8, 28)


def test_an_on_time_run_uses_the_current_day():
    """20:00 IST Thursday, the scheduled case: nothing changes."""
    on_time = datetime(2026, 8, 27, 14, 30, tzinfo=timezone.utc)  # 20:00 IST
    assert clock.session_date(on_time) == D(2026, 8, 27)


def test_the_publish_hour_is_the_boundary():
    """NSE publishes ~18:00-19:00 IST. Before it, today has nothing to fetch."""
    just_before = datetime(2026, 8, 27, 12, 29, tzinfo=timezone.utc)  # 17:59 IST
    just_after = datetime(2026, 8, 27, 12, 31, tzinfo=timezone.utc)   # 18:01 IST
    assert clock.session_date(just_before) == D(2026, 8, 26)
    assert clock.session_date(just_after) == D(2026, 8, 27)


def test_a_weekend_run_reports_the_preceding_friday():
    """The watchdog runs daily, including Saturday and Sunday."""
    saturday = datetime(2026, 8, 29, 10, 38, tzinfo=timezone.utc)
    sunday = datetime(2026, 8, 30, 4, 0, tzinfo=timezone.utc)
    assert clock.session_date(saturday) == D(2026, 8, 28)
    assert clock.session_date(sunday) == D(2026, 8, 28)


def test_a_monday_dawn_run_reaches_back_over_the_weekend():
    """Monday 05:00 IST: Friday's is the newest published session, not
    Sunday's and not Monday's."""
    monday_dawn = datetime(2026, 8, 30, 23, 30, tzinfo=timezone.utc)  # Mon 05:00 IST
    assert monday_dawn.astimezone(clock.IST).date() == D(2026, 8, 31)
    assert clock.session_date(monday_dawn) == D(2026, 8, 28)


def test_session_date_never_returns_a_weekend():
    """Property: no caller should ever be handed a non-trading day."""
    from datetime import timedelta
    start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
    for hours in range(0, 24 * 45):
        assert not clock.is_weekend(clock.session_date(start + timedelta(hours=hours)))
