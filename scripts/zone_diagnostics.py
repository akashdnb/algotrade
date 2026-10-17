from __future__ import annotations

import argparse
import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import polars as pl

from algotrade.config import settings as settings_mod
from algotrade.services import panel as panel_service
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

    loaded = panel_service.load(
        curated_dir=settings.curated_dir,
        actions_dir=settings.data_dir / "raw" / "actions",
        through=args.date,
        sessions=None,
        min_sessions=1,
    )
    panel = loaded.panel
    as_of = args.date or loaded.last_session
    panel = panel.filter(pl.col("date") <= as_of)
    if args.symbol:
        panel = panel.filter(pl.col("symbol") == args.symbol.upper())

    latest = (
        panel.sort("date")
        .group_by("symbol", maintain_order=True)
        .agg(pl.col("close").last().alias("latest_close"))
        .filter(pl.col("latest_close") >= config.min_price)
        .select("symbol")
    )
    eligible = set(latest["symbol"].to_list())
    panel = panel.filter(pl.col("symbol").is_in(eligible))

    rows = []
    for symbol in sorted(eligible):
        daily = panel.filter(pl.col("symbol") == symbol).sort("date")
        candles = daily if timeframe == Timeframe.DAILY else aggregate_to_weekly(daily) if timeframe == Timeframe.WEEKLY else aggregate_to_monthly(daily)
        required = config.min_sessions_history if timeframe == Timeframe.DAILY else config.atr_period + config.base_max_candles + config.expansion_max_candles
        if candles["date"].n_unique() < required:
            continue
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
            print(f"{item['symbol']} {item['date']} {item['direction'] or '-'} {item['status']} {item['reason']} base={item['base_start_date']}..{item['base_end_date']} base_range={item['base_range']} atr={item['atr']} disp_atr={item['displacement_atr']} volume={item['volume_ratio']} expansion={item['expansion_candles']} end={item['displacement_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
