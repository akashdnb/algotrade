"""Daily market-data ingestion -- reconciliation-driven.

**The unit of work is the gap, not "today".** A run never asks "did my cron
fire?"; it asks "which sessions between the last one on disk and the target
date are missing?" and fetches all of them. That single choice is what makes a
dropped, delayed, or disabled schedule self-repairing: Wednesday's run fixes
Tuesday because Wednesday asks the question, not because anything remembered
Tuesday failed.

Holiday detection is empirical, not predicted. NSE's archive returning **404**
for a date is a direct statement that no session exists -- unlike `nsefin`'s
holiday calendar, which fails silently and reported `False` for Independence Day
(PROJECT.md §10.9). So a 404 is recorded as a holiday and is not an error.

Storage follows the tiering in the Phase 2 design:

* raw session CSVs land in `data/sessions/YYYY/` -- append-only, immutable once
  written, committed to git. This is the audit trail AND what keeps the
  repository active, defeating the 60-day scheduled-workflow disable.
* the curated/adjusted panel is a **cache**, rebuilt from those inputs and
  never committed.

MongoDB is not touched here beyond the `job_runs` ledger. Market data never
enters the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

from algotrade.platform import clock

log = logging.getLogger(__name__)

#: How far back to look for gaps when the store is not empty. A month covers
#: any plausible outage; beyond that use `--bootstrap` or `recover.yml`, where
#: the cost of a long scan is expected rather than incurred nightly.
DEFAULT_LOOKBACK_DAYS: int = 31


@dataclass(frozen=True, slots=True)
class SessionOutcome:
    day: date
    status: str          # fetched | holiday | failed | cached
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("fetched", "holiday", "cached")


@dataclass(slots=True)
class IngestResult:
    outcomes: list[SessionOutcome] = field(default_factory=list)

    @property
    def fetched(self) -> list[date]:
        return [o.day for o in self.outcomes if o.status == "fetched"]

    @property
    def holidays(self) -> list[date]:
        return [o.day for o in self.outcomes if o.status == "holiday"]

    @property
    def failed(self) -> list[SessionOutcome]:
        return [o for o in self.outcomes if o.status == "failed"]

    @property
    def ok(self) -> bool:
        return not self.failed

    def summary(self) -> dict:
        return {
            "considered": len(self.outcomes),
            "fetched": len(self.fetched),
            "holidays": len(self.holidays),
            "failed": len(self.failed),
        }


class IngestionService:
    """Fetch missing sessions onto disk. No parsing decisions, no domain logic.

    `fetch` is injected rather than imported so the reconciliation logic can be
    tested without a network, and so a future alternative source is a
    constructor argument rather than a rewrite.
    """

    def __init__(
        self,
        *,
        sessions_dir: Path,
        fetch: Callable[[date, Path], Path],
        is_holiday_error: Callable[[Exception], bool],
        archive_dirs: Iterable[Path] = (),
    ) -> None:
        self._sessions_dir = Path(sessions_dir)
        self._fetch = fetch
        self._is_holiday_error = is_holiday_error
        self._archive_dirs = [Path(p) for p in archive_dirs]

    # ------------------------------------------------------------ discovery --

    def path_for(self, day: date) -> Path:
        """Year-partitioned so a directory listing stays usable for decades."""
        return self._sessions_dir / f"{day.year}" / f"sec_bhavdata_full_{day:%d%m%Y}.csv"

    def have(self, day: date) -> bool:
        if self.path_for(day).exists():
            return True
        name = f"sec_bhavdata_full_{day:%d%m%Y}.csv"
        return any((d / name).exists() for d in self._archive_dirs)

    def stored_dates(self) -> list[date]:
        days: list[date] = []
        for root in [self._sessions_dir, *self._archive_dirs]:
            if not root.exists():
                continue
            for path in root.rglob("sec_bhavdata_full_*.csv"):
                stem = path.stem.replace("sec_bhavdata_full_", "")
                try:
                    days.append(date(int(stem[4:8]), int(stem[2:4]), int(stem[0:2])))
                except (ValueError, IndexError):
                    continue
        return sorted(set(days))

    def last_stored(self) -> date | None:
        days = self.stored_dates()
        return days[-1] if days else None

    def missing_sessions(
        self, *, through: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS
    ) -> list[date]:
        """Weekdays up to `through` with no file on disk.

        Starts from the day after the newest stored session, so a long-running
        system scans a handful of days rather than its whole history. Weekends
        are excluded; holidays are NOT, because they are indistinguishable from
        a gap until NSE is asked -- the fetch discovers that and records it.
        """
        last = self.last_stored()
        start = (
            last + timedelta(days=1)
            if last
            else through - timedelta(days=lookback_days)
        )
        # Guard a clock skew or a hand-edited file dated in the future.
        if start > through:
            return []
        floor = through - timedelta(days=lookback_days)
        if last is None and start < floor:
            start = floor
        return [d for d in clock.weekdays_between(start, through) if not self.have(d)]

    # ------------------------------------------------------------- fetching --

    def ingest(
        self, *, through: date, lookback_days: int = DEFAULT_LOOKBACK_DAYS
    ) -> IngestResult:
        """Fetch every missing session up to `through`.

        Continues past a failure rather than aborting: one unavailable session
        should not prevent the other four from being repaired, and the failure
        is reported for the caller to decide about.
        """
        result = IngestResult()
        targets = self.missing_sessions(through=through, lookback_days=lookback_days)
        if not targets:
            log.info("no missing sessions through %s", through)
            return result

        log.info("%d missing session(s): %s", len(targets),
                 ", ".join(str(d) for d in targets))

        for day in targets:
            destination = self.path_for(day)
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._fetch(day, destination.parent)
            except Exception as exc:  # noqa: BLE001 -- classified immediately below
                if self._is_holiday_error(exc):
                    # Not an error: NSE stating there was no session. This is
                    # the only holiday source in the stack that has not lied.
                    log.info("%s: no session (holiday)", day)
                    result.outcomes.append(
                        SessionOutcome(day, "holiday", str(exc))
                    )
                else:
                    log.warning("%s: fetch failed: %s", day, exc)
                    result.outcomes.append(
                        SessionOutcome(day, "failed", f"{type(exc).__name__}: {exc}")
                    )
                continue

            if destination.exists():
                result.outcomes.append(SessionOutcome(day, "fetched"))
            else:
                # The fetcher reported success but wrote nothing under the name
                # we expect. Treating that as success would leave a permanent
                # gap that `missing_sessions` re-attempts forever.
                result.outcomes.append(SessionOutcome(
                    day, "failed", f"no file at {destination} after a successful fetch"
                ))
        return result


def nse_ingestion_service(sessions_dir: Path, archive_dirs: Iterable[Path] = ()):
    """The production wiring: fetch from NSE's archive.

    Reuses `algotrade.io.nse`, which already handles NSE's
    CSV-that-is-actually-an-XLSX-in-a-ZIP, the referer requirement, retries,
    and atomic writes.
    """
    from algotrade.io import nse

    return IngestionService(
        sessions_dir=sessions_dir,
        fetch=lambda day, directory: nse.download_to(day, directory),
        is_holiday_error=lambda exc: isinstance(exc, nse.NseFetchError)
        and "404" in str(exc),
        archive_dirs=archive_dirs,
    )
