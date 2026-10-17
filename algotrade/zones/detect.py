from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from .config import DemandSupplyConfig, Timeframe, ZoneType
from .metrics import atr, candle_metrics


def _windows(frame: pl.DataFrame, config: DemandSupplyConfig):
    rows = frame.sort("date").to_dicts()
    for i in range(config.base_max_candles, len(rows)):
        displacement = rows[i]
        if displacement["date"] > config_as_of:
            break
        yield rows, i


def detect_zones(candles: pl.DataFrame, timeframe: Timeframe, config: DemandSupplyConfig, as_of_date: date) -> list[dict[str, Any]]:
    if config.timeframe != timeframe:
        raise ValueError("config timeframe does not match requested timeframe")
    if candles.is_empty():
        return []
    visible = candles.filter(pl.col("date") <= as_of_date).sort("date")
    if visible.height <= config.atr_period:
        return []
    frame = candle_metrics(atr(visible, config.atr_period)).sort("date")
    rows = frame.to_dicts()
    zones: list[dict[str, Any]] = []
    for i in range(1, len(rows)):
        d = rows[i]
        atr_value = d.get("atr")
        if atr_value is None or atr_value <= 0:
            continue
        candidates = []
        for length in range(1, min(config.base_max_candles, i) + 1):
            base = rows[i-length:i]
            base_range = max(x["high"] for x in base) - min(x["low"] for x in base)
            if base_range > config.base_range_atr_multiplier * float(atr_value):
                break
            if any(x["body_ratio"] < config.minimum_body_ratio for x in base):
                break
            candidates.append(base)
        if not candidates:
            continue
        base = candidates[-1]
        direction = "bull" if d["close"] > d["open"] else "bear"
        displacement = (d["close"] - min(x["low"] for x in base)) if direction == "bull" else (max(x["high"] for x in base) - d["close"])
        displacement_atr = displacement / float(atr_value)
        if displacement_atr < config.minimum_displacement_atr:
            continue
        avg_volume = sum(float(x.get("volume") or 0) for x in base) / len(base)
        volume_ratio = (float(d.get("volume") or 0) / avg_volume) if avg_volume else None
        if config.volume_enabled and (volume_ratio is None or volume_ratio < config.minimum_volume_ratio):
            continue
        if config.structure_confirmation_required:
            if direction == "bull" and i > 0 and d["close"] <= max(x["high"] for x in rows[:i]):
                continue
            if direction == "bear" and i > 0 and d["close"] >= min(x["low"] for x in rows[:i]):
                continue
        zone_type = ZoneType.DEMAND if direction == "bull" else ZoneType.SUPPLY
        zones.append({
            "zone_type": zone_type,
            "zone_low": min(x["low"] for x in base),
            "zone_high": max(x["high"] for x in base),
            "base_start_date": base[0]["date"],
            "base_end_date": base[-1]["date"],
            "displacement_date": d["date"],
            "atr_at_creation": float(atr_value),
            "displacement_atr": float(displacement_atr),
            "volume_ratio": volume_ratio,
            "base_quality": max(0.0, 1.0 - (max(x["high"] for x in base) - min(x["low"] for x in base)) / (config.base_range_atr_multiplier * float(atr_value))),
            "structure": 1.0,
            "created_at": d["date"],
        })
    return zones
