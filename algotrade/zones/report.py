from __future__ import annotations
import argparse, json
from datetime import date
from decimal import Decimal
from pathlib import Path
from algotrade.config import settings as settings_mod, validation
from algotrade.repositories import client
from algotrade.repositories.zones import ZoneRepository
from algotrade.services import panel as panel_service
from .config import load_configs
from .proximity import calculate_zone_distance, classify_zone_proximity

def render_zones_summary(results):
    lines=["Zones daily summary"]
    for result in results:
        lines.append(f"{result.symbol} · ₹{result.current_price} · " + ", ".join(f"{tf.value}:{len(zones)}" for tf,zones in result.zones.items()))
    return "\n".join(lines)

def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--symbol",required=True); parser.add_argument("--date",type=date.fromisoformat); parser.add_argument("--json",action="store_true")
    args=parser.parse_args(); settings=settings_mod.load(); report=validation.validate(settings)
    if report.errors: return 2
    configs=load_configs(Path(__file__).resolve().parents[2]/"config/zones/zones_v1.yaml")
    db=client.connect(settings.mongodb_uri,database=settings.mongodb_database); repo=ZoneRepository(db)
    zones=repo.active_for_symbol(args.symbol)
    if not zones: return 1
    loaded=panel_service.load(curated_dir=settings.curated_dir,actions_dir=settings.data_dir/"raw"/"actions",through=args.date,sessions=2,min_sessions=1)
    latest=loaded.panel.filter((loaded.panel["symbol"]==args.symbol) & (loaded.panel["date"]<= (args.date or loaded.panel["date"].max()))).sort("date").tail(1)
    if latest.is_empty(): return 1
    price=Decimal(str(latest["close"][0])).quantize(Decimal("0.01"))
    payload=[]
    for zone in zones:
        distance,pct=calculate_zone_distance(zone,price); proximity=classify_zone_proximity(zone,price,configs[zone.timeframe])
        payload.append({"zone_id":zone.zone_id,"timeframe":zone.timeframe.value,"type":zone.zone_type.value,"low":str(zone.zone_low),"high":str(zone.zone_high),"distance_pct":pct,"status":proximity.value,"score":zone.strength_score,"touch_count":zone.touch_count})
    if args.json: print(json.dumps({"symbol":args.symbol,"price":str(price),"zones":payload},indent=2)); return 0
    for z in payload: print(f"{z['timeframe'].upper()} / {z['type'].title()}: ₹{z['low']}–₹{z['high']} / Distance: {z['distance_pct']:.2f}% / Status: {z['status'].upper()} / Score: {z['score']} / Touches: {z['touch_count']}")
    return 0
if __name__=="__main__": raise SystemExit(main())
