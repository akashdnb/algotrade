from __future__ import annotations

from datetime import date
from typing import Any

import polars as pl

from .config import DemandSupplyConfig, Timeframe, ZoneType
from .metrics import atr, candle_metrics


def _base_candidates(rows: list[dict[str, Any]], i: int, config: DemandSupplyConfig) -> list[list[dict[str, Any]]]:
    candidates: list[list[dict[str, Any]]] = []
    for length in range(1, min(config.base_max_candles, i) + 1):
        base = rows[i - length:i]
        base_range = max(x["high"] for x in base) - min(x["low"] for x in base)
        if base_range > config.base_range_atr_multiplier * float(rows[i]["atr"]):
            break
        if any(x["body_ratio"] < config.minimum_body_ratio for x in base):
            break
        candidates.append(base)
    return candidates


def _volume_ratio(base: list[dict[str, Any]], move: list[dict[str, Any]]) -> float | None:
    avg_base = sum(float(x.get("volume") or 0) for x in base) / len(base)
    avg_move = sum(float(x.get("volume") or 0) for x in move) / len(move)
    return avg_move / avg_base if avg_base else None


def _structure_ok(rows: list[dict[str, Any]], end_index: int, direction: str, close: float) -> bool:
    prior = rows[:end_index]
    if not prior:
        return True
    if direction == "bull":
        return close > max(x["high"] for x in prior)
    return close < min(x["low"] for x in prior)


def _zone(
    *,
    base: list[dict[str, Any]],
    move: list[dict[str, Any]],
    atr_value: float,
    direction: str,
    displacement_atr: float,
    volume_ratio: float | None,
) -> dict[str, Any]:
    base_low = min(x["low"] for x in base)
    base_high = max(x["high"] for x in base)
    base_range = base_high - base_low
    return {
        "zone_type": ZoneType.DEMAND if direction == "bull" else ZoneType.SUPPLY,
        "zone_low": base_low,
        "zone_high": base_high,
        "base_start_date": base[0]["date"],
        "base_end_date": base[-1]["date"],
        "displacement_date": move[-1]["date"],
        "atr_at_creation": float(atr_value),
        "displacement_atr": float(displacement_atr),
        "volume_ratio": volume_ratio,
        "base_quality": max(0.0, 1.0 - base_range / (1.25 * float(atr_value))),
        "structure": 1.0,
        "created_at": move[-1]["date"],
        "expansion_candles": len(move),
    }


def _diagnostic(
    *,
    timeframe: Timeframe,
    row: dict[str, Any],
    base: list[dict[str, Any]] | None,
    status: str,
    reason: str,
    direction: str | None = None,
    move: list[dict[str, Any]] | None = None,
    displacement_atr: float | None = None,
    volume_ratio: float | None = None,
) -> dict[str, Any]:
    return {
        "timeframe": timeframe.value,
        "date": row["date"],
        "direction": direction,
        "status": status,
        "reason": reason,
        "base_start_date": base[0]["date"] if base else None,
        "base_end_date": base[-1]["date"] if base else None,
        "base_candles": len(base) if base else 0,
        "base_range": (max(x["high"] for x in base) - min(x["low"] for x in base)) if base else None,
        "atr": float(row["atr"]) if row.get("atr") is not None else None,
        "displacement_atr": displacement_atr,
        "volume_ratio": volume_ratio,
        "expansion_candles": len(move) if move else 0,
    }


def diagnose_zones(
    candles: pl.DataFrame,
    timeframe: Timeframe,
    config: DemandSupplyConfig,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Return candidate diagnostics, including rejection reasons, without lookahead."""
    if config.timeframe != timeframe:
        raise ValueError("config timeframe does not match requested timeframe")
    if candles.is_empty():
        return []
    visible = candles.filter(pl.col("date") <= as_of_date).sort("date")
    if visible.height <= config.atr_period:
        return []
    rows = candle_metrics(atr(visible, config.atr_period)).sort("date").to_dicts()
    diagnostics: list[dict[str, Any]] = []
    for i in range(1, len(rows)):
        row = rows[i]
        atr_value = row.get("atr")
        if atr_value is None or atr_value <= 0:
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=None, status="rejected", reason="atr_unavailable"))
            continue
        candidates = _base_candidates(rows, i, config)
        if not candidates:
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=None, status="rejected", reason="no_valid_base"))
            continue
        base = candidates[-1]
        direction = "bull" if row["close"] > row["open"] else "bear" if row["close"] < row["open"] else None
        if direction is None:
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="doji_displacement", direction=None))
            continue
        if config.detection_mode == "displacement":
            move = [row]
            base_low = min(x["low"] for x in base)
            base_high = max(x["high"] for x in base)
            displacement_move = row["close"] - base_low if direction == "bull" else base_high - row["close"]
            displacement_atr = displacement_move / float(atr_value)
            if displacement_atr < config.minimum_displacement_atr:
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="displacement_below_threshold", direction=direction, move=move, displacement_atr=displacement_atr))
                continue
            volume_ratio = _volume_ratio(base, move)
            if config.volume_enabled and (volume_ratio is None or volume_ratio < config.minimum_volume_ratio):
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="volume_below_threshold", direction=direction, move=move, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
                continue
            if config.structure_confirmation_required and not _structure_ok(rows, i, direction, row["close"]):
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="structure_not_confirmed", direction=direction, move=move, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
                continue
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="accepted", reason="zone_detected", direction=direction, move=move, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
            continue

        found = False
        for length in range(1, min(config.expansion_max_candles, len(rows) - i) + 1):
            move = rows[i:i + length]
            if any((x["close"] <= x["open"]) if direction == "bull" else (x["close"] >= x["open"]) for x in move):
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="expansion_direction_failed", direction=direction, move=move))
                continue
            end = move[-1]
            base_low = min(x["low"] for x in base)
            base_high = max(x["high"] for x in base)
            displacement_move = end["close"] - base_low if direction == "bull" else base_high - end["close"]
            expansion_atr = displacement_move / float(end["atr"] or atr_value)
            if expansion_atr < config.minimum_expansion_atr:
                if length == config.expansion_max_candles:
                    diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="expansion_below_threshold", direction=direction, move=move, displacement_atr=expansion_atr))
                continue
            volume_ratio = _volume_ratio(base, move)
            if config.volume_enabled and (volume_ratio is None or volume_ratio < config.minimum_volume_ratio):
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="volume_below_threshold", direction=direction, move=move, displacement_atr=expansion_atr, volume_ratio=volume_ratio))
                found = True
                break
            if config.structure_confirmation_required and not _structure_ok(rows, i + length - 1, direction, end["close"]):
                diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="structure_not_confirmed", direction=direction, move=move, displacement_atr=expansion_atr, volume_ratio=volume_ratio))
                found = True
                break
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="accepted", reason="zone_detected", direction=direction, move=move, displacement_atr=expansion_atr, volume_ratio=volume_ratio))
            found = True
            break
        if not found and not any(d["date"] == row["date"] and d["status"] == "rejected" for d in diagnostics[-config.expansion_max_candles:]):
            diagnostics.append(_diagnostic(timeframe=timeframe, row=row, base=base, status="rejected", reason="expansion_not_found", direction=direction))
    return diagnostics


def detect_zones(
    candles: pl.DataFrame,
    timeframe: Timeframe,
    config: DemandSupplyConfig,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Detect supply/demand zones using only candles visible at ``as_of_date``."""
    diagnostics = diagnose_zones(candles, timeframe, config, as_of_date)
    zones: list[dict[str, Any]] = []
    for item in diagnostics:
        if item["status"] != "accepted":
            continue
        row = candles.filter(pl.col("date") == item["date"]).sort("date").tail(1)
        if row.is_empty():
            continue
        base_start = item["base_start_date"]
        base_end = item["base_end_date"]
        base = candles.filter((pl.col("date") >= base_start) & (pl.col("date") <= base_end)).sort("date").to_dicts()
        move = candles.filter(pl.col("date") >= base_end).sort("date")
        move_rows = move.filter(pl.col("date") <= item["date"]).to_dicts()
        direction = item["direction"]
        atr_value = float(item["atr"] or 0)
        zones.append(_zone(base=base, move=move_rows[-max(1, item["expansion_candles"]):], atr_value=atr_value, direction=direction, displacement_atr=float(item["displacement_atr"] or 0), volume_ratio=item["volume_ratio"]))
    return zones
