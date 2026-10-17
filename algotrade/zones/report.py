from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from algotrade.config import settings as settings_mod
from algotrade.config import validation
from algotrade.io import store
from algotrade.repositories import client
from algotrade.repositories.zones import ZoneRepository
from algotrade.services import panel as panel_service
from algotrade.zones.config import Timeframe, load_configs
from algotrade.zones.pipeline import ZONES_MIN_SESSIONS, run_for_symbol
from algotrade.zones.proximity import calculate_zone_distance, classify_zone_proximity

REPO = Path(__file__).resolve().parents[2]


def _day(text: str) -> date:
    return datetime.strptime(text, "%Y-%m-%d").date()


def render_zones_summary(results, *, config=None) -> str:
    lines = ["Zones daily summary"]
    for result in results:
        lines.append(f"{result.symbol} · ₹{result.current_price}")
        for timeframe, zones in result.zones.items():
            for zone in sorted(zones, key=lambda item: item.strength_score, reverse=True):
                _, distance_pct = calculate_zone_distance(zone, result.current_price)
                proximity = classify_zone_proximity(zone, result.current_price, config).value.upper() if config else zone.status.value.upper()
                lines.append(f"{timeframe.value.upper()} / {zone.zone_type.value.title()}: ₹{zone.zone_low}–₹{zone.zone_high} / Distance: {distance_pct:.2f}% / Status: {proximity} / Score: {zone.strength_score} / Touches: {zone.touch_count}")
    return "\n".join(lines)


def _current_price(settings, symbol: str, as_of: date | None):
    panel = store.read(settings.curated_dir, end=as_of)
    if panel.is_empty() or "symbol" not in panel.columns:
        return None
    rows = panel.filter(panel["symbol"] == symbol).sort("date")
    if rows.is_empty():
        return None
    return rows.tail(1)["close"][0]


def _payload(symbol: str, as_of: date | None, zones: list, current_price=None) -> dict:
    visible = [z for z in zones if as_of is None or z.created_at <= as_of]
    return {
        "symbol": symbol,
        "as_of": as_of.isoformat() if as_of else None,
        "current_price": str(current_price) if current_price is not None else None,
        "zone_count": len(visible),
        "zones": [{
            "zone_id": z.zone_id, "timeframe": z.timeframe.value, "type": z.zone_type.value,
            "low": str(z.zone_low), "high": str(z.zone_high), "status": z.status.value,
            "strength_score": z.strength_score, "touch_count": z.touch_count,
            "created_at": z.created_at.isoformat(), "invalidated_at": z.invalidated_at.isoformat() if z.invalidated_at else None,
        } for z in visible],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only demand/supply zone report")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--date", type=_day)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    settings = settings_mod.load()
    report = validation.validate(settings)
    blocking = [f for f in report.errors if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    symbol = args.symbol.upper()
    configs = load_configs(REPO / "config/zones/zones_v1.yaml")
    if args.date is not None:
        loaded = panel_service.load(
            curated_dir=settings.curated_dir,
            actions_dir=settings.data_dir / "raw" / "actions",
            through=args.date,
            sessions=None,
            min_sessions=ZONES_MIN_SESSIONS,
        )
        result = run_for_symbol(loaded.panel, symbol, configs=configs, as_of_date=args.date)
        if result is None:
            return 1
        zones = result.all_zones
        current_price = result.current_price
    else:
        db = client.connect(settings.mongodb_uri, database=settings.mongodb_database)
        zones = ZoneRepository(db).for_symbol(symbol)
        current_price = _current_price(settings, symbol, None)

    payload = _payload(symbol, args.date, zones, current_price)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Zones report · {payload['symbol']}")
        if payload["as_of"]:
            print(f"As of: {payload['as_of']}")
        if payload["current_price"]:
            print(f"Current price: ₹{payload['current_price']}")
        print(f"Zones: {payload['zone_count']}")
        for zone in payload["zones"]:
            config = configs[Timeframe(zone["timeframe"])]
            zone_obj = next(z for z in zones if z.zone_id == zone["zone_id"])
            _, distance = calculate_zone_distance(zone_obj, current_price)
            proximity = classify_zone_proximity(zone_obj, current_price, config).value.upper()
            print(f"  {zone['timeframe'].upper()} / {zone['type'].title()}: ₹{zone['low']}–₹{zone['high']} / Distance: {distance:.2f}% / Status: {proximity} / Score: {zone['strength_score']} / Touches: {zone['touch_count']}")
    return 0 if payload["zone_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
