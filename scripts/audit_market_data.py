#!/usr/bin/env python
"""Audit raw NSE sessions and the curated panel for data-integrity anomalies.

This is deliberately deterministic and does not silently repair data. It reports:
  * filename/content-date rejections;
  * missing/duplicate trading sessions;
  * duplicate (date, symbol) rows;
  * invalid OHLC relationships and non-positive prices;
  * curated-panel freshness and session/row reconciliation;
  * fund/ETF symbols that the zone universe must exclude.

Run from the repository root:
    python scripts/audit_market_data.py --through 2026-10-16
    python scripts/audit_market_data.py --symbol RELIANCE --through 2026-10-16

External web verification is intentionally separate: this audit proves internal
consistency; it does not claim that every NSE row has been independently checked
against a third-party website.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.core.universe import DEFAULT_EXCLUSIONS, looks_like_fund  # noqa: E402
from algotrade.io.archive import discover, load_panel  # noqa: E402
from algotrade.io import store  # noqa: E402


def _day(text: str) -> date:
    return date.fromisoformat(text)


def _expected_weekdays(start: date, end: date) -> set[date]:
    days = (end - start).days + 1
    return {
        start + timedelta(days=i)
        for i in range(days)
        if (start + timedelta(days=i)).weekday() < 5
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=_day)
    parser.add_argument("--through", type=_day)
    parser.add_argument("--symbol")
    args = parser.parse_args()

    settings = settings_mod.load()
    end = args.through or date.today()

    print("== raw session audit ==")
    chosen = discover([*settings.archive_dirs, settings.sessions_dir])
    dates = sorted(d for d in chosen if d <= end and (args.start is None or d >= args.start))
    if not dates:
        print("no sessions found")
        return 1

    panel, report = load_panel(
        [*settings.archive_dirs, settings.sessions_dir],
        start=args.start,
        end=end,
    )
    print(f"sessions loaded: {report.sessions_loaded:,}")
    print(f"rows loaded:     {report.rows:,}")
    print(f"rejected:        {len(report.rejected):,}")
    for day, reason in sorted(report.rejected.items()):
        print(f"  REJECT {day}: {reason}")

    if panel.is_empty():
        return 1

    dupes = (
        panel.group_by(["date", "symbol"])
        .len()
        .filter(pl.col("len") > 1)
    )
    print(f"duplicate (date,symbol) rows: {dupes.height:,}")
    if dupes.height:
        print(dupes.head(20))

    invalid = panel.filter(
        (pl.col("open") <= 0)
        | (pl.col("high") <= 0)
        | (pl.col("low") <= 0)
        | (pl.col("close") <= 0)
        | (pl.col("high") < pl.max_horizontal("open", "close", "low"))
        | (pl.col("low") > pl.min_horizontal("open", "close", "high"))
    )
    print(f"invalid OHLC rows:           {invalid.height:,}")
    if invalid.height:
        print(invalid.select("date", "symbol", "open", "high", "low", "close").head(50))

    sessions = set(dates)
    if len(dates) >= 2:
        weekdays = _expected_weekdays(dates[0], dates[-1])
        gaps = sorted(weekdays - sessions)
        print(f"weekday gaps (not NSE-holiday aware): {len(gaps):,}")
        if gaps:
            print("  " + ", ".join(map(str, gaps[:50])))

    if args.symbol:
        symbol = args.symbol.upper()
        symbol_df = panel.filter(pl.col("symbol") == symbol).sort("date")
        print(f"\n== {symbol} ==")
        print(f"rows: {symbol_df.height:,}")
        if symbol_df.height:
            print(f"first: {symbol_df['date'].min()}")
            print(f"last:  {symbol_df['date'].max()}")
            print(symbol_df.select("date", "open", "high", "low", "close", "volume").tail(20))

    print("\n== curated panel audit ==")
    try:
        curated = store.read(settings.curated_dir)
    except Exception as exc:
        print(f"curated read failed: {exc}")
    else:
        max_date = curated["date"].max() if not curated.is_empty() else None
        print(f"curated max date: {max_date}")
        print(f"curated rows:    {curated.height:,}")
        if max_date != end:
            print(f"ANOMALY: curated panel is stale by requested through date {end}")

    symbols = set(panel["symbol"].unique().to_list())
    fund_like = sorted(s for s in symbols if s in DEFAULT_EXCLUSIONS or looks_like_fund(s))
    print(f"fund/ETF symbols present in raw panel: {len(fund_like):,}")
    if fund_like:
        print("  " + ", ".join(fund_like[:100]))

    print("\nAudit complete. A non-zero count is a finding, not automatically a corruption:")
    print("holiday filename/content mismatches are expected mirror defects and are rejected deliberately.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
