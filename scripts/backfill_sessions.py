#!/usr/bin/env python
"""Find sessions missing from the mirrored archive and fetch them from NSE.

The mirror drops real trading sessions (PROJECT.md §10.8 -- two in a 39-session
sample). Those gaps corrupt every trailing-window feature that spans them, so
this repairs them.

The repair verifies itself. A missing session breaks the prev_close chain for
~97% of symbols on the following day; once backfilled, those breaks must
disappear. So the same check that finds the gap confirms the fix, and a
backfilled file that is wrong shows up as breaks that fail to heal.

    python scripts/backfill_sessions.py --start 2022-07-01 --end 2022-08-31
    python scripts/backfill_sessions.py --all --dry-run
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.core import integrity as ig  # noqa: E402
from algotrade.io import nse  # noqa: E402
from algotrade.io.archive import load_panel  # noqa: E402

MIRROR = REPO / "data" / "raw" / "nse-data-bank" / "data"
MIRROR_OLD = REPO / "data" / "raw" / "nse-data-bank" / "historic_data"
BACKFILL = REPO / "data" / "raw" / "backfill"


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def gaps(panel: pl.DataFrame) -> list[date]:
    """Sessions absent from the panel, inferred from broad chain breaks.

    `find_missing_sessions` reports the date *after* each gap along with the
    last session each symbol saw. The missing session lies strictly between
    them; when more than one session is absent consecutively we can only
    identify the boundary, so those are reported for manual follow-up.
    """
    missing = ig.find_missing_sessions(panel)
    if missing.is_empty():
        return []
    found: list[date] = []
    for row in missing.iter_rows(named=True):
        # Candidate dates are weekdays strictly between gap_after and date.
        span = pl.date_range(
            row["gap_after"], row["date"], interval="1d", eager=True
        ).to_list()
        found.extend(
            d for d in span[1:-1] if d.weekday() < 5  # NSE trades Mon-Fri
        )
    return sorted(set(found))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_day, help="first session to consider (YYYY-MM-DD)")
    ap.add_argument("--end", type=_day, help="last session to consider (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="scan the whole archive")
    ap.add_argument("--dry-run", action="store_true", help="report gaps, fetch nothing")
    ap.add_argument("--delay", type=float, default=nse.DEFAULT_DELAY)
    args = ap.parse_args()

    if not args.all and not (args.start and args.end):
        ap.error("pass --all, or both --start and --end")

    print("loading archive ...", flush=True)
    panel, report = load_panel(
        [MIRROR, MIRROR_OLD, BACKFILL], start=args.start, end=args.end
    )
    if panel.is_empty():
        print("no sessions found -- is the archive cloned?")
        return 1

    print(
        f"  {report.sessions_loaded:,} sessions, {report.rows:,} rows, "
        f"{panel['symbol'].n_unique():,} symbols"
    )
    if report.rejected:
        print(
            f"  {len(report.rejected)} stale-duplicate files skipped "
            f"(holiday artefacts, §10.8)"
        )

    candidates = gaps(panel)
    if not candidates:
        print("\nno missing sessions detected.")
        return 0

    print(f"\n{len(candidates)} candidate missing session(s):")
    for day in candidates:
        print(f"  {day}  ({day:%a})")

    if args.dry_run:
        print("\n--dry-run: nothing fetched.")
        return 0

    print(f"\nfetching from NSE into {BACKFILL.relative_to(REPO)} ...")
    session = nse._session()
    fetched, holidays, failed = [], [], []
    for day in candidates:
        try:
            path = nse.download_to(day, BACKFILL, session=session, delay=args.delay)
        except nse.NseFetchError as exc:
            # A 404 means NSE has no session for that date -- it was a holiday
            # the mirror never had, not a gap. That is a clean answer.
            (holidays if "404" in str(exc) else failed).append((day, exc))
            print(f"  {day}  -- {exc}")
        else:
            print(f"  {day}  -> {path.name}")
            fetched.append(day)

    if not fetched:
        print("\nnothing fetched; nothing to verify.")
        return 0 if not failed else 1

    # --- self-verification -------------------------------------------------
    print("\nre-checking the chain with backfilled sessions ...")
    healed, hreport = load_panel(
        [MIRROR, MIRROR_OLD, BACKFILL], start=args.start, end=args.end
    )
    before = ig.find_chain_breaks(panel).height
    after = ig.find_chain_breaks(healed).height
    residue = ig.unexplained_chain_breaks(healed).height

    print(f"  sessions   {report.sessions_loaded:,} -> {hreport.sessions_loaded:,}")
    print(f"  breaks     {before:,} -> {after:,}")
    print(f"  unexplained after repair: {residue:,}")

    if after >= before:
        print("\nWARNING: breaks did not fall. Backfilled data may be wrong.")
        return 1
    print(f"\nrepaired {len(fetched)} session(s); {before - after:,} breaks resolved.")
    if holidays:
        print(f"{len(holidays)} candidate(s) were non-sessions (404) -- genuine holidays.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
