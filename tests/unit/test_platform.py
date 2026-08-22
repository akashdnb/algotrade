"""Tests for clock and configuration."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from algotrade.platform import clock, config

D = date


# ------------------------------------------------------------------ clock ---


def test_the_business_date_is_ist_not_utc():
    """20:00 IST on the 30th is 14:30 UTC on the 30th -- but 00:30 IST on the
    1st is 19:00 UTC on the previous day. Deriving the business date from UTC
    puts every post-18:30-IST session on the wrong date."""
    c = clock.FixedClock(datetime(2026, 9, 30, 19, 0, tzinfo=timezone.utc))
    assert c.today() == D(2026, 10, 1)          # 00:30 IST on 1 Oct
    assert c.now().date() == D(2026, 9, 30)     # still 30 Sep in UTC


def test_fixed_clock_refuses_a_naive_instant():
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.FixedClock(datetime(2026, 9, 30, 14, 30))


def test_system_clock_returns_an_aware_utc_instant():
    now = clock.SystemClock().now()
    assert now.tzinfo is timezone.utc


def test_weekends_are_identified():
    assert clock.is_weekend(D(2026, 10, 3))     # Saturday
    assert clock.is_weekend(D(2026, 10, 4))     # Sunday
    assert not clock.is_weekend(D(2026, 10, 2))  # Friday


def test_previous_weekday_skips_the_weekend():
    assert clock.previous_weekday(D(2026, 10, 5)) == D(2026, 10, 2)   # Mon -> Fri


def test_weekdays_between_is_inclusive_and_excludes_weekends():
    days = clock.weekdays_between(D(2026, 10, 1), D(2026, 10, 7))
    assert days == [D(2026, 10, 1), D(2026, 10, 2), D(2026, 10, 5),
                    D(2026, 10, 6), D(2026, 10, 7)]


def test_period_of_formats_as_yyyy_mm():
    assert clock.period_of(D(2026, 9, 30)) == "2026-09"
    assert clock.period_of(D(2026, 12, 1)) == "2026-12"


# ----------------------------------------------------------------- config ---


def test_a_missing_mongodb_uri_is_a_hard_error_not_a_default():
    with pytest.raises(config.ConfigError, match="MONGODB_URI"):
        config.load("local", config_dir="config", environ={})


def test_env_selects_the_database():
    s = config.load("paper", environ={"MONGODB_URI": "mongodb://x"})
    assert s.database == "algotrade_paper"
    s = config.load("live", environ={"MONGODB_URI": "mongodb://x"})
    assert s.database == "algotrade_live"


def test_a_live_job_pointed_at_the_paper_database_is_refused():
    """The most expensive configuration mistake available here."""
    with pytest.raises(config.ConfigError, match="refusing"):
        config.load("live", environ={"MONGODB_URI": "mongodb://x",
                                     "MONGODB_DATABASE": "algotrade_paper"})


def test_an_unknown_env_is_rejected():
    with pytest.raises(config.ConfigError, match="unknown env"):
        config.load("staging", environ={"MONGODB_URI": "mongodb://x"})


def test_env_defaults_from_the_environment_then_to_local():
    s = config.load(environ={"MONGODB_URI": "mongodb://x", "ALGOTRADE_ENV": "paper"})
    assert s.env == "paper"
    s = config.load(environ={"MONGODB_URI": "mongodb://x"})
    assert s.env == "local"


def test_credentials_are_redacted_for_logging():
    s = config.load("local", environ={
        "MONGODB_URI": "mongodb+srv://user:secret@cluster.mongodb.net/db"})
    out = s.redacted()
    assert "secret" not in str(out)
    assert "user" not in str(out)
    assert "cluster.mongodb.net" in out["mongodb_uri"]


def test_a_uri_without_credentials_is_left_alone():
    s = config.load("local", environ={"MONGODB_URI": "mongodb://localhost:27017"})
    assert s.redacted()["mongodb_uri"] == "mongodb://localhost:27017"


def test_dry_run_is_off_unless_explicitly_enabled():
    assert not config.load("local", environ={"MONGODB_URI": "m"}).dry_run
    assert config.load("local", environ={"MONGODB_URI": "m",
                                         "ALGOTRADE_DRY_RUN": "true"}).dry_run


def test_the_commit_is_picked_up_from_github_actions():
    s = config.load("local", environ={"MONGODB_URI": "m", "GITHUB_SHA": "abc123"})
    assert s.code_commit == "abc123"
