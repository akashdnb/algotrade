#!/usr/bin/env python
"""Fetch NSE daily index closes for every session in the panel (§11d).

One file per session, each carrying every index. Dates come from the equity
panel, so only real trading sessions are requested.

    python scripts/fetch_indices.py
"""
from __future__ import annotations
import argparse, sys, time
from datetime import date, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from algotrade.io import indices, nse, store  # noqa: E402


def _day(t: str) -> date:
    return datetime.strptime(t, "%Y-%m-%d").date()


ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--start", type=_day)
ap.add_argument("--end", type=_day)
ap.add_argument("--delay", type=float, default=0.7)
args = ap.parse_args()

panel = store.read(REPO / "data/curated", columns=["date"])
sessions = sorted(panel["date"].unique().to_list())
if args.start:
    sessions = [d for d in sessions if d >= args.start]
if args.end:
    sessions = [d for d in sessions if d <= args.end]

out = REPO / "data/raw/indices"
sess = nse._session()
rep = indices.SyncReport()
for i, day in enumerate(sessions, 1):
    target = out / indices.filename_for(day)
    if target.exists():
        rep.reused.append(day)
        continue
    try:
        got = indices.download_to(day, out, session=sess)
        (rep.fetched if got else rep.missing).append(day)
    except Exception as exc:
        rep.failed[str(day)] = f"{type(exc).__name__}: {exc}"
    time.sleep(args.delay)
    if i % 250 == 0:
        print(f"  {i}/{len(sessions)}  fetched={len(rep.fetched)} "
              f"missing={len(rep.missing)} failed={len(rep.failed)}", flush=True)

print(f"sessions {len(sessions):,} | fetched {len(rep.fetched):,} | "
      f"reused {len(rep.reused):,} | no-file {len(rep.missing):,} | "
      f"failed {len(rep.failed):,}")
for k, v in list(rep.failed.items())[:5]:
    print(f"  FAIL {k}: {v[:100]}")
