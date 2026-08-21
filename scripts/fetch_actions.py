#!/usr/bin/env python
"""Cache NSE's corporate-action history to disk (PROJECT.md §11 task A).

    python scripts/fetch_actions.py --start 2010-06-01 --end 2026-08-31
"""
from __future__ import annotations
import argparse, sys
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from algotrade.io import actions as store  # noqa: E402


def _day(t: str) -> date:
    return datetime.strptime(t, "%Y-%m-%d").date()


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--start", type=_day, required=True)
ap.add_argument("--end", type=_day, required=True)
ap.add_argument("--refetch", action="store_true")
args = ap.parse_args()

rep = store.sync(args.start, args.end, directory=REPO / "data/raw/actions",
                 refetch=args.refetch)
print(f"fetched {len(rep.fetched)} quarter(s), reused {len(rep.reused)}, "
      f"failed {len(rep.failed)}")
for label, err in rep.failed.items():
    print(f"  FAIL {label}: {err[:110]}")
print(f"cached rows total: {rep.rows:,}")

frame = store.load(REPO / "data/raw/actions")
print(f"loaded {frame.height:,} unique actions, "
      f"{frame['ex_date'].min()} .. {frame['ex_date'].max()}")
print(f"distinct symbols: {frame['symbol'].n_unique():,} | "
      f"with ISIN: {store.isin_map(frame).height:,}")
