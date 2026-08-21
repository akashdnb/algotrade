"""Load many bhavcopy sessions from one or more directories on disk.

Handles the mirror's known defects (PROJECT.md §10.8) without hiding them:

  * 76 of 3,995 mirrored files are stale duplicates named after NSE holidays.
    `bhavcopy.read_file` rejects them via its filename/content date guard; they
    are recorded here as skips rather than aborting the load.
  * Sessions absent from the mirror can be backfilled into a separate
    directory. Later directories win, so a backfilled session overrides a
    stale mirrored one of the same date.

Discovery is by filename so that a date range can be selected without opening
every file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence

import polars as pl

from algotrade.io.bhavcopy import BhavcopyError, date_from_filename, read_file

# Both eras, either directory layout.
_PATTERNS: tuple[str, ...] = ("sec_bhavdata_full_*.csv", "cm*bhav.csv")


@dataclass
class LoadReport:
    """What happened during a load. Defects are data, not log noise."""

    sessions_loaded: int = 0
    rows: int = 0
    rejected: dict[date, str] = field(default_factory=dict)
    superseded: dict[date, Path] = field(default_factory=dict)

    @property
    def rejected_dates(self) -> list[date]:
        return sorted(self.rejected)


def discover(
    dirs: Sequence[str | Path],
    patterns: Iterable[str] = _PATTERNS,
) -> dict[date, Path]:
    """Map each trading date to the file that should be used for it.

    Directories are searched in order and later ones win, so a backfill
    directory listed after the mirror overrides it.
    """
    chosen: dict[date, Path] = {}
    for directory in dirs:
        root = Path(directory)
        if not root.is_dir():
            continue
        for pattern in patterns:
            for path in sorted(root.glob(pattern)):
                try:
                    chosen[date_from_filename(path)] = path
                except BhavcopyError:
                    continue  # not a bhavcopy file; ignore quietly
    return chosen


def load_panel(
    dirs: Sequence[str | Path],
    *,
    start: date | None = None,
    end: date | None = None,
    series: Iterable[str] | None = ("EQ",),
    patterns: Iterable[str] = _PATTERNS,
) -> tuple[pl.DataFrame, LoadReport]:
    """Read every session in `dirs` within [start, end] into one panel.

    Files whose contents disagree with their filename's date are skipped and
    recorded in the report -- that guard is what catches the mirror's stale
    holiday duplicates, so suppressing it would reintroduce phantom sessions.

    Returns the panel sorted by (date, symbol), plus a report of what was
    loaded and what was rejected.
    """
    by_date = discover(dirs, patterns)
    wanted = sorted(
        d
        for d in by_date
        if (start is None or d >= start) and (end is None or d <= end)
    )

    report = LoadReport()
    # Record where a later directory displaced the primary one, so a backfill
    # that shadows a mirrored file stays visible rather than silent.
    if len(dirs) > 1:
        primary = Path(dirs[0])
        report.superseded = {
            day: by_date[day]
            for day in wanted
            if by_date[day].parent != primary
        }

    frames: list[pl.DataFrame] = []
    for day in wanted:
        try:
            frames.append(read_file(by_date[day], series=series))
        except BhavcopyError as exc:
            # Strip the filename prefix read_file adds; the date is the key.
            report.rejected[day] = str(exc).split(": ", 1)[-1]

    if not frames:
        return pl.DataFrame(), report

    panel = pl.concat(frames).sort(["date", "symbol"])
    report.sessions_loaded = len(frames)
    report.rows = panel.height
    return panel, report
