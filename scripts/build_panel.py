#!/usr/bin/env python
"""Build the curated Parquet panel from the raw archive (PROJECT.md §11 task C).

Reads every bhavcopy session, writes it partitioned by year, and records a
manifest of what went in. Git versions code, not data, and the mirror updates
daily -- so without the manifest an identical-code rerun after a backfill
silently produces different numbers.

    python scripts/build_panel.py
    python scripts/build_panel.py --start 2020-01-01
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.io import store  # noqa: E402
from algotrade.io.archive import load_panel  # noqa: E402

MIRROR = REPO / "data" / "raw" / "nse-data-bank" / "data"
BACKFILL = REPO / "data" / "raw" / "backfill"
CURATED = REPO / "data" / "curated"

# The cm*bhav mirror is an ISIN source only -- its dates are a strict subset of
# sec_bhavdata_full's and it lacks VWAP and delivery data (§10.7).
PANEL_DIRS = [MIRROR, BACKFILL]


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", type=_day)
    ap.add_argument("--end", type=_day)
    ap.add_argument("--out", type=Path, default=CURATED)
    args = ap.parse_args()

    print("reading raw archive ...", flush=True)
    t0 = time.perf_counter()
    panel, report = load_panel(PANEL_DIRS, start=args.start, end=args.end)
    read_secs = time.perf_counter() - t0

    if panel.is_empty():
        print("no sessions found -- is the archive cloned?")
        return 1

    print(
        f"  {report.sessions_loaded:,} sessions, {report.rows:,} rows "
        f"in {read_secs:.1f}s"
    )
    if report.rejected:
        print(
            f"  {len(report.rejected)} stale-duplicate sessions rejected "
            f"(holiday artefacts, §10.8)"
        )

    print(f"writing parquet to {args.out.relative_to(REPO)} ...", flush=True)
    manifest = store.write(
        panel,
        directory=args.out,
        rejected=report.rejected,
        source_dirs=[str(p.relative_to(REPO)) for p in PANEL_DIRS],
    )

    t0 = time.perf_counter()
    back = store.read(args.out)
    load_secs = time.perf_counter() - t0

    size_mb = sum(f.stat().st_size for f in args.out.rglob("*.parquet")) / 1e6
    print(f"  {size_mb:.0f} MB across {manifest.sessions:,} sessions")
    print(f"  {manifest.first_session} .. {manifest.last_session}, "
          f"{manifest.symbols:,} symbols")
    print(f"\nread speed: {read_secs:.1f}s from CSV -> {load_secs:.1f}s from parquet "
          f"({read_secs / max(load_secs, 0.01):.0f}x faster)")

    if back.height != panel.height:
        print(f"\nWARNING: roundtrip mismatch {panel.height:,} -> {back.height:,}")
        return 1
    print("roundtrip verified: row counts match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
