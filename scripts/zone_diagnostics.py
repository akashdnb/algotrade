from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import polars as pl

from algotrade.config import settings as settings_mod
from algotrade.io import store
from algotrade.zones.aggregation import aggregate_to_monthly, aggregate_to_weekly
from algotrade.zones.config import Timeframe, load_configs
from algotrade.zones.detect import diagnose_zones

REPO = Path(__file__).resolve().parents[1]


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="Explain demand/supply candidate acceptance and rejection")
    parser.add_argument("--symbol", help="one symbol; omit to inspect the whole eligible panel")
    parser.add_argument("--timeframe", choices=[x.value for x in Timeframe], default="monthly")
    parser.add_argument("--mode", choices=["configured", "displacement", "expansion"], default="configured")
    parser.add_argument("--date", type=_day)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = settings_mod.load()
    configs = load_configs(REPO / "config/zones/zones_v1.yaml")
    timeframe = Timeframe(args.timeframe)
    config = configs[timeframe]
    if args.mode != "configured":
        config = replace(config, detection_mode=args.mode)

    # Default to the latest session actually present in the curated panel.
    # Using date.today() can create a one-session mismatch with the production
    # job on weekends/holidays (for example, data through Friday vs Saturday).
    panel = store.read(settings.curated_dir, end=args.date)
    if panel.is_empty():
        print("No curated panel data available")
        return 1
    latest_session = panel["date"].max()
    as_of = args.date or latest_session
    panel = panel.filter(pl.col("date") <= as_of)
    if args.symbol:
        panel = panel.filter(pl.col("symbol") == args.symbol.upper())
    panel = panel.filter(pl.col("close") >= config.min_price)

    rows = []
    for symbol in panel.get_column("symbol").unique().sort().to_list():
        daily = panel.filter(pl.col("symbol") == symbol).sort("date")
        if daily.height < config.min_sessions_history:
            continue
        candles = (
            daily
            if timeframe == Timeframe.DAILY
            else aggregate_to_weekly(daily) if timeframe == Timeframe.WEEKLY
            else aggregate_to_monthly(daily)
        )
        diagnostics = diagnose_zones(candles, timeframe, config, as_of)
        for item in diagnostics:
            item = dict(item)
            item["symbol"] = symbol
            rows.append(item)

    if args.json:
        print(json.dumps(rows, default=str, indent=2))
        return 0

    accepted = [r for r in rows if r["status"] == "accepted"]
    rejected = [r for r in rows if r["status"] == "rejected"]
    print(f"Zone diagnostics | timeframe={timeframe.value} mode={config.detection_mode} as_of={as_of}")
    print(f"symbols={len({r['symbol'] for r in rows})} accepted={len(accepted)} rejected={len(rejected)}")
    reasons = {}
    for item in rejected:
        reasons[item["reason"]] = reasons.get(item["reason"], 0) + 1
    print("rejection reasons:")
    for reason, count in sorted(reasons.items(), key=lambda x: (-x[1], x[0])):
        print(f"  {reason}: {count}")
    if args.symbol:
        for item in rows:
            print(
                f"{item['symbol']} {item['date']} {item['direction'] or '-'} "
                f"{item['status']} {item['reason']} "
                f"base={item['base_start_date']}..{item['base_end_date']} "
                f"base_range={item['base_range']} atr={item['atr']} "
                f"disp_atr={item['displacement_atr']} volume={item['volume_ratio']} "
                f"expansion={item['expansion_candles']} end={item['displacement_date']}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
