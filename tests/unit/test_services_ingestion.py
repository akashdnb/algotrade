"""Tests for reconciliation-driven ingestion.

The behaviour under test is the one the whole scheduling design rests on: the
unit of work is the GAP, not "today", so a dropped or delayed cron repairs
itself on the next run with no human involved.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from algotrade.services.ingestion import IngestionService, SessionOutcome

D = date


class FakeNse:
    """Records what was asked for; can be told which dates are holidays."""

    def __init__(self, holidays: set[date] = frozenset(),
                 failures: set[date] = frozenset()):
        self.holidays = set(holidays)
        self.failures = set(failures)
        self.requested: list[date] = []

    def fetch(self, day: date, directory: Path) -> Path:
        self.requested.append(day)
        if day in self.holidays:
            raise RuntimeError(f"{day}: 404 (likely not a trading session)")
        if day in self.failures:
            raise ConnectionError("network down")
        path = directory / f"sec_bhavdata_full_{day:%d%m%Y}.csv"
        path.write_text("SYMBOL,SERIES,CLOSE\nTCS,EQ,100\n", encoding="utf-8")
        return path


def _service(tmp_path: Path, nse: FakeNse, archive_dirs=()) -> IngestionService:
    return IngestionService(
        sessions_dir=tmp_path / "sessions",
        fetch=nse.fetch,
        is_holiday_error=lambda e: "404" in str(e),
        archive_dirs=archive_dirs,
    )


def _seed(service: IngestionService, days: list[date]) -> None:
    for day in days:
        path = service.path_for(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")


# ------------------------------------------------------------- discovery ---


def test_files_are_year_partitioned():
    service = IngestionService(sessions_dir=Path("/s"), fetch=lambda d, p: p,
                               is_holiday_error=lambda e: False)
    assert service.path_for(D(2026, 9, 30)) == Path(
        "/s/2026/sec_bhavdata_full_30092026.csv"
    )


def test_stored_dates_are_parsed_back_out_of_the_filenames(tmp_path: Path):
    service = _service(tmp_path, FakeNse())
    _seed(service, [D(2026, 9, 28), D(2026, 9, 30)])
    assert service.stored_dates() == [D(2026, 9, 28), D(2026, 9, 30)]
    assert service.last_stored() == D(2026, 9, 30)


def test_the_frozen_archive_counts_as_stored(tmp_path: Path):
    """The 16-year archive lives outside git; ingestion must not re-fetch it."""
    archive = tmp_path / "archive"
    archive.mkdir()
    (archive / "sec_bhavdata_full_30092026.csv").write_text("x")

    service = _service(tmp_path, FakeNse(), archive_dirs=[archive])
    assert service.have(D(2026, 9, 30))
    assert service.last_stored() == D(2026, 9, 30)


# --------------------------------------------------- the reconciliation ----


def test_the_gap_is_the_unit_of_work_not_today(tmp_path: Path):
    """Three sessions missing after an outage; one run repairs all three."""
    nse = FakeNse()
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])            # Tuesday

    missing = service.missing_sessions(through=D(2026, 10, 2))
    assert missing == [D(2026, 9, 30), D(2026, 10, 1), D(2026, 10, 2)]

    result = service.ingest(through=D(2026, 10, 2))
    assert result.fetched == missing
    assert result.ok


def test_weekends_are_never_requested(tmp_path: Path):
    nse = FakeNse()
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 10, 2)])            # Friday
    service.ingest(through=D(2026, 10, 5))      # Monday
    assert nse.requested == [D(2026, 10, 5)]    # Sat/Sun skipped entirely


def test_an_already_stored_session_is_not_refetched(tmp_path: Path):
    nse = FakeNse()
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 30)])
    result = service.ingest(through=D(2026, 9, 30))
    assert nse.requested == []
    assert result.outcomes == []


def test_running_twice_is_idempotent(tmp_path: Path):
    nse = FakeNse()
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])

    first = service.ingest(through=D(2026, 9, 30))
    second = service.ingest(through=D(2026, 9, 30))
    assert first.fetched == [D(2026, 9, 30)]
    assert second.fetched == []
    assert nse.requested == [D(2026, 9, 30)]


def test_a_first_run_with_no_history_is_bounded_by_the_lookback(tmp_path: Path):
    """Otherwise an empty store would try to fetch from the epoch."""
    nse = FakeNse()
    service = _service(tmp_path, nse)
    missing = service.missing_sessions(through=D(2026, 9, 30), lookback_days=7)
    assert missing[0] >= D(2026, 9, 23)
    assert missing[-1] == D(2026, 9, 30)


def test_a_future_dated_file_does_not_produce_a_negative_range(tmp_path: Path):
    """Clock skew or a hand-edited filename must not crash the nightly job."""
    service = _service(tmp_path, FakeNse())
    _seed(service, [D(2027, 1, 1)])
    assert service.missing_sessions(through=D(2026, 9, 30)) == []


# ----------------------------------------------------- holidays vs errors ---


def test_a_404_is_a_holiday_and_not_a_failure(tmp_path: Path):
    """NSE's 404 is the only holiday source in this stack that has not lied
    (§10.9 -- nsefin returned False for Independence Day)."""
    nse = FakeNse(holidays={D(2026, 10, 2)})     # Gandhi Jayanti, a Friday
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 10, 1)])

    result = service.ingest(through=D(2026, 10, 2))
    assert result.holidays == [D(2026, 10, 2)]
    assert result.failed == []
    assert result.ok


def test_a_network_failure_is_a_failure(tmp_path: Path):
    nse = FakeNse(failures={D(2026, 9, 30)})
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])

    result = service.ingest(through=D(2026, 9, 30))
    assert not result.ok
    assert result.failed[0].day == D(2026, 9, 30)
    assert "ConnectionError" in result.failed[0].detail


def test_one_failure_does_not_abandon_the_other_sessions(tmp_path: Path):
    """A single unavailable session must not block repairing the rest."""
    nse = FakeNse(failures={D(2026, 9, 30)})
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])

    result = service.ingest(through=D(2026, 10, 2))
    assert result.fetched == [D(2026, 10, 1), D(2026, 10, 2)]
    assert [o.day for o in result.failed] == [D(2026, 9, 30)]


def test_a_failed_session_is_retried_on_the_next_run(tmp_path: Path):
    nse = FakeNse(failures={D(2026, 9, 30)})
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])
    service.ingest(through=D(2026, 9, 30))

    nse.failures.clear()
    result = service.ingest(through=D(2026, 9, 30))
    assert result.fetched == [D(2026, 9, 30)]


def test_a_holiday_is_re_examined_rather_than_cached_as_absent(tmp_path: Path):
    """No file is written for a holiday, so the next run asks again. That costs
    one request and avoids a wrong 'holiday' becoming permanent."""
    nse = FakeNse(holidays={D(2026, 9, 30)})
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 29)])
    service.ingest(through=D(2026, 9, 30))
    service.ingest(through=D(2026, 9, 30))
    assert nse.requested == [D(2026, 9, 30), D(2026, 9, 30)]


def test_a_fetch_that_writes_nothing_is_reported_as_failed(tmp_path: Path):
    """Reporting success would leave a permanent gap that the reconciliation
    re-attempts on every run forever, silently."""
    service = IngestionService(
        sessions_dir=tmp_path / "sessions",
        fetch=lambda day, directory: directory,   # writes no file
        is_holiday_error=lambda e: False,
    )
    _seed(service, [D(2026, 9, 29)])
    result = service.ingest(through=D(2026, 9, 30))
    assert not result.ok
    assert "no file" in result.failed[0].detail


def test_the_summary_counts_each_category(tmp_path: Path):
    nse = FakeNse(holidays={D(2026, 10, 2)}, failures={D(2026, 10, 1)})
    service = _service(tmp_path, nse)
    _seed(service, [D(2026, 9, 30)])
    result = service.ingest(through=D(2026, 10, 2))
    assert result.summary() == {
        "considered": 2, "fetched": 0, "holidays": 1, "failed": 1,
    }
