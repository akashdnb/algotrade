from __future__ import annotations

from datetime import date
from dataclasses import replace

from .config import DemandSupplyConfig, ZoneStatus, ZoneType


def _invalidated(zone, candle, config):
    buffer = float(zone.atr_at_creation) * config.invalidation_buffer_atr
    if zone.zone_type == ZoneType.DEMAND:
        threshold = float(zone.zone_low) - buffer
        return float(candle["low"] if config.invalidation_mode == "wick" else candle["close"]) < threshold
    threshold = float(zone.zone_high) + buffer
    return float(candle["high"] if config.invalidation_mode == "wick" else candle["close"]) > threshold


def invalidate_zone(zone, candle, config: DemandSupplyConfig) -> bool:
    return _invalidated(zone, candle, config)


def register_zone_test(zone, tested_at: date):
    return replace(zone, status=ZoneStatus.TESTED, touch_count=zone.touch_count + 1, first_tested_at=zone.first_tested_at or tested_at, last_tested_at=tested_at)


def update_zone_status(zone, candles_since_creation, config: DemandSupplyConfig):
    current = zone
    was_in_zone = False
    for candle in candles_since_creation.sort("date").to_dicts():
        if candle["date"] <= zone.created_at:
            continue
        if invalidate_zone(current, candle, config):
            return replace(current, status=ZoneStatus.BROKEN, invalidated_at=candle["date"])
        low, high = float(candle["low"]), float(candle["high"])
        in_zone = low <= float(zone.zone_high) and high >= float(zone.zone_low)
        if in_zone and not was_in_zone:
            current = register_zone_test(current, candle["date"])
        was_in_zone = in_zone
    return current
