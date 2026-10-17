from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from .config import DemandSupplyConfig, Timeframe, ZoneType
from .metrics import atr, candle_metrics


def detect_zones(
    candles: pl.DataFrame,
    timeframe: Timeframe,
    config: DemandSupplyConfig,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Detect supply/demand zones using only candles visible at ``as_of_date``."""
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
        displacement = rows[i]
        atr_value = displacement.get("atr")
        if atr_value is None or atr_value <= 0:
            continue

        candidates = []
        for length in range(1, min(config.base_max_candles, i) + 1):
            base = rows[i - length:i]
            base_range = max(x["high"] for x in base) - min(x["low"] for x in base)
            if base_range > config.base_range_atr_multiplier * float(atr_value):
                break
            if any(x["body_ratio"] < config.minimum_body_ratio for x in base):
                break
            candidates.append(base)
        if not candidates:
            continue

        base = candidates[-1]
        base_low = min(x["low"] for x in base)
        base_high = max(x["high"] for x in base)
        direction = "bull" if displacement["close"] > displacement["open"] else "bear"
        displacement_move = (
            displacement["close"] - base_low
            if direction == "bull"
            else base_high - displacement["close"]
        )
        displacement_atr = displacement_move / float(atr_value)
        if displacement_atr < config.minimum_displacement_atr:
            continue

        avg_volume = sum(float(x.get("volume") or 0) for x in base) / len(base)
        volume_ratio = (
            float(displacement.get("volume") or 0) / avg_volume
            if avg_volume
            else None
        )
        if config.volume_enabled and (
            volume_ratio is None or volume_ratio < config.minimum_volume_ratio
        ):
            continue

        structure_score = 1.0
        if config.structure_confirmation_required:
            prior = rows[:i]
            if direction == "bull" and prior and displacement["close"] <= max(x["high"] for x in prior):
                continue
            if direction == "bear" and prior and displacement["close"] >= min(x["low"] for x in prior):
                continue
        zone_type = ZoneType.DEMAND if direction == "bull" else ZoneType.SUPPLY
        base_quality = max(
            0.0,
            1.0 - base_range / (config.base_range_atr_multiplier * float(atr_value)),
        )
        zones.append(
            {
                "zone_type": zone_type,
                "zone_low": base_low,
                "zone_high": base_high,
                "base_start_date": base[0]["date"],
                "base_end_date": base[-1]["date"],
                "displacement_date": displacement["date"],
                "atr_at_creation": float(atr_value),
                "displacement_atr": float(displacement_atr),
                "volume_ratio": volume_ratio,
                "base_quality": base_quality,
                "structure": structure_score,
                "created_at": displacement["date"],
            }
        )
    return zones
