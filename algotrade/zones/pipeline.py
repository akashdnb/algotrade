from __future__ import annotations
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import polars as pl
from .aggregation import aggregate_to_monthly, aggregate_to_weekly
from .config import DemandSupplyConfig, Timeframe
from .detect import detect_zones
from .lifecycle import update_zone_status
from .scoring import score_zone
from .models import Zone

ZONES_MIN_SESSIONS = 400

@dataclass(frozen=True, slots=True)
class SymbolZoneResult:
    symbol: str
    current_price: Decimal
    zones: dict[Timeframe, list[Zone]]
    confluence: list = ()
    @property
    def all_zones(self):
        return [z for values in self.zones.values() for z in values]


def _frame(daily, timeframe):
    if timeframe == Timeframe.DAILY: return daily
    if timeframe == Timeframe.WEEKLY: return aggregate_to_weekly(daily)
    return aggregate_to_monthly(daily)


def run_for_symbol(daily_candles, symbol, *, configs, as_of_date, current_price=None):
    daily = daily_candles.filter(pl.col("symbol") == symbol).sort("date")
    if daily.is_empty(): return None
    current_price = Decimal(str(current_price if current_price is not None else daily.filter(pl.col("date") <= as_of_date).tail(1)["close"][0])).quantize(Decimal("0.01"))
    output = {}
    for tf, config in configs.items():
        frame = _frame(daily, tf)
        raw = detect_zones(frame, tf, config, as_of_date)
        zones = []
        for item in raw:
            zone = Zone(symbol, tf, item["zone_type"], Decimal(str(item["zone_low"])).quantize(Decimal("0.01")), Decimal(str(item["zone_high"])).quantize(Decimal("0.01")), item["base_start_date"], item["base_end_date"], item["displacement_date"], Decimal(str(item["atr_at_creation"])).quantize(Decimal("0.01")), item["displacement_atr"], item["volume_ratio"], 0, None, 0, None, None, item["created_at"], None, item.get("base_quality", .5), item.get("structure", 1.0))
            history = frame.filter((pl.col("date") >= zone.created_at) & (pl.col("date") <= as_of_date))
            zone = update_zone_status(zone, history, config)
            zone = Zone(**{**zone.__dict__, "strength_score": score_zone(zone, config=config)}) if hasattr(zone, "__dict__") else __import__("dataclasses").replace(zone, strength_score=score_zone(zone, config=config))
            zones.append(zone)
        output[tf] = zones
    return SymbolZoneResult(symbol, current_price, output)


def run_for_universe(panel, *, configs, as_of_date):
    if panel.is_empty(): return []
    symbols = sorted(panel.filter(pl.col("date") <= as_of_date)["symbol"].unique().to_list())
    out = []
    for symbol in symbols:
        result = run_for_symbol(panel, symbol, configs=configs, as_of_date=as_of_date)
        if result is not None and result.current_price >= Decimal(str(max(c.min_price for c in configs.values()))):
            out.append(result)
    return out
