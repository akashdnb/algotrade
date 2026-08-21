"""Cache NSE's corporate-action history to disk.

`nse.fetch_corporate_actions` hits NSE live. Research reloads the action history
constantly, so it is cached here -- NSE must not be a dependency hit repeatedly,
and an unavailable endpoint should not stall research.

Cached per calendar quarter, because that gives deterministic filenames: a
quarter is either present or missing, so resuming after an interruption needs no
bookkeeping. The **current** quarter is always refetched, since it is still
accumulating.

Stored as raw JSON exactly as NSE returned it. Parsing and factor extraction
stay in `core.actions`, so a change of interpretation never requires refetching.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Final

import polars as pl

from algotrade.io import nse

DEFAULT_DIR: Final[Path] = Path("data/raw/actions")


def quarter_of(day: date) -> tuple[int, int]:
    return day.year, (day.month - 1) // 3 + 1


def quarter_bounds(year: int, quarter: int) -> tuple[date, date]:
    start_month = 3 * (quarter - 1) + 1
    start = date(year, start_month, 1)
    end = (
        date(year + 1, 1, 1) if quarter == 4 else date(year, start_month + 3, 1)
    )
    return start, date.fromordinal(end.toordinal() - 1)


def quarters_between(start: date, end: date) -> list[tuple[int, int]]:
    """Every (year, quarter) touching the inclusive range."""
    out: list[tuple[int, int]] = []
    year, quarter = quarter_of(start)
    last = quarter_of(end)
    while (year, quarter) <= last:
        out.append((year, quarter))
        year, quarter = (year + 1, 1) if quarter == 4 else (year, quarter + 1)
    return out


def cache_path(directory: Path, year: int, quarter: int) -> Path:
    return Path(directory) / f"actions_{year}Q{quarter}.json"


@dataclass
class SyncReport:
    fetched: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    rows: int = 0


def sync(
    start: date,
    end: date,
    *,
    directory: Path | str = DEFAULT_DIR,
    today: date | None = None,
    refetch: bool = False,
    delay: float = nse.DEFAULT_DELAY,
) -> SyncReport:
    """Ensure every quarter in [start, end] is cached, fetching what is missing.

    Args:
        today: Which quarter counts as incomplete. Injected rather than read
            from the clock so the behaviour is testable.
        refetch: Refetch quarters already present.

    A quarter that fails is recorded and the sync continues -- one bad window
    should not lose the rest of a 65-call run.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    current = quarter_of(today or date.today())
    report = SyncReport()
    session = nse._session()

    for year, quarter in quarters_between(start, end):
        label = f"{year}Q{quarter}"
        path = cache_path(directory, year, quarter)
        # The live quarter is still accumulating, so a cached copy is stale.
        stale = (year, quarter) == current
        if path.exists() and not refetch and not stale:
            report.reused.append(label)
            continue

        q_start, q_end = quarter_bounds(year, quarter)
        try:
            rows = nse.fetch_corporate_actions(
                max(q_start, start), min(q_end, end), session=session, delay=delay
            )
        except nse.NseFetchError as exc:
            report.failed[label] = str(exc)
            continue

        # Write via a temp file so an interrupted run cannot leave a truncated
        # quarter that later looks complete.
        staging = path.with_suffix(".json.part")
        staging.write_text(json.dumps(rows), encoding="utf-8")
        staging.replace(path)
        report.fetched.append(label)

    report.rows = sum(
        len(json.loads(p.read_text())) for p in sorted(directory.glob("actions_*.json"))
    )
    return report


def load(
    directory: Path | str = DEFAULT_DIR,
    *,
    start: date | None = None,
    end: date | None = None,
) -> pl.DataFrame:
    """Read all cached actions into a frame with a parsed `ex_date`.

    Rows whose `exDate` cannot be parsed are dropped: NSE occasionally emits
    blanks and a null ex-date cannot be reconciled against anything.
    """
    directory = Path(directory)
    rows: list[dict] = []
    for path in sorted(directory.glob("actions_*.json")):
        rows.extend(json.loads(path.read_text()))
    if not rows:
        return nse.actions_to_frame([])

    frame = nse.actions_to_frame(rows).filter(pl.col("ex_date").is_not_null())
    if start is not None:
        frame = frame.filter(pl.col("ex_date") >= start)
    if end is not None:
        frame = frame.filter(pl.col("ex_date") <= end)
    # NSE repeats rows across overlapping windows.
    return frame.unique(
        subset=["symbol", "ex_date", "subject"], keep="first"
    ).sort(["ex_date", "symbol"])


def isin_map(actions: pl.DataFrame) -> pl.DataFrame:
    """symbol -> ISIN with the latest ex-date each pairing was seen on.

    Used to bridge renames that `integrity.find_renames` cannot see because the
    old and new symbols are separated by a trading gap (§10.10). Any stock that
    ever had a corporate action appears here, which is most of them.

    `last_ex_date` is what lets `core.reconcile.chains_from_isin` orient a
    rename: the feed reports today's symbol, so for a given ISIN the symbol on
    the most recent ex-date is the current one.
    """
    return (
        actions.filter(
            (pl.col("isin").str.len_chars() > 0) & (pl.col("symbol").str.len_chars() > 0)
        )
        .group_by("symbol", "isin")
        .agg(pl.col("ex_date").max().alias("last_ex_date"))
        .sort(["isin", "last_ex_date", "symbol"])
    )
