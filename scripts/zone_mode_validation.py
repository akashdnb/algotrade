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
from algotrade.zones.detect import detect_zones

REPO = Path(__file__).resolve().parents[1]


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare displacement and expansion zone detection")
    parser.add_argument("--timeframe", choices=[x.value for x in Timeframe], default="monthly")
    parser.add_argument("--date", type=_day)
    parser.add_argument("--symbol")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = settings_mod.load()
    as_of = args.date or date.today()
    timeframe = Timeframe(args.timeframe)
    base_config = load_configs(REPO / "config/zones/zones_v1.yaml")[timeframe]
    panel = store.read(settings.curated_dir, end=as_of)
    if args.symbol:
        panel = panel.filter(pl.col("symbol") == args.symbol.upper())
    panel = panel.filter(pl.col("close") >= base_config.min_price)

    rows = []
    for symbol in panel.get_column("symbol").unique().sort().to_list():
        daily = panel.filter(pl.col("symbol") == symbol).sort("date")
        if daily.height < base_config.min_sessions_history:
            continue
        candles = daily if timeframe == Timeframe.DAILY else aggregate_to_weekly(daily) if timeframe == Timeframe.WEEKLY else aggregate_to_monthly(daily)
        result = {"symbol": symbol}
        for mode in ("displacement", "expansion"):
            config = replace(base_config, detection_mode=mode)
            zones = detect_zones(candles, timeframe, config, as_of)
            demands = [z for z in zones if z["zone_type"].value == "demand"]
            supplies = [z for z in zones if z["zone_type"].value == "supply"]
            widths = [(z["zone_high"] - z["zone_low"]) / z["zone_low"] * 100 for z in zones if z["zone_low"]]
            result[mode] = {
                "zones": len(zones),
                "demand": len(demands),
                "supply": len(supplies),
                "median_width_pct": sorted(widths)[len(widths) // 2] if widths else None,
            }
        rows.append(result)

    summary = {}
    for mode in ("displacement", "expansion"):
        summary[mode] = {
            "symbols": len(rows),
            "zero_demand": sum(r[mode]["demand"] == 0 for r in rows),
            "total_demand": sum(r[mode]["demand"] for r in rows),
            "total_supply": sum(r[mode]["supply"] for r in rows),
        }

    payload = {"timeframe": timeframe.value, "as_of": as_of.isoformat(), "summary": summary, "symbols": rows}
    if args.json:
        print(json.dumps(payload, default=str, indent=2))
    else:
        print(f"Zone mode validation | timeframe={timeframe.value} as_of={as_of}")
        for mode, stats in summary.items():
            zero_pct = 100 * stats["zero_demand"] / stats["symbols"] if stats["symbols"] else 0
            print(f"  {mode}: symbols={stats['symbols']} zero_demand={stats['zero_demand']} ({zero_pct:.1f}%) demand={stats['total_demand']} supply={stats['total_supply']}")
        if args.symbol:
            print(json.dumps(rows[0] if rows else {}, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
