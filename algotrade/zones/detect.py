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


def _expansion_direction_check(
    move: list[dict[str, Any]],
    direction: str,
    base_low: float,
    base_high: float,
    config: DemandSupplyConfig,
) -> tuple[bool, str]:
    """Validate expansion direction using configurable candle/net semantics."""
    directional = sum(
        1
        for candle in move
        if (candle["close"] > candle["open"] if direction == "bull" else candle["close"] < candle["open"])
    )
    opposite = len(move) - directional

    if config.expansion_direction_mode == "strict":
        if directional < len(move):
            return False, "directional_candle_count_failed"
        if opposite and not config.expansion_allow_opposite_candles:
            return False, "opposite_candle_failed"
        return True, "ok"

    if config.expansion_direction_mode == "majority":
        if directional < config.expansion_min_directional_candles:
            return False, "directional_candle_count_failed"
        if directional <= len(move) // 2:
            return False, "directional_candle_count_failed"
        if opposite and not config.expansion_allow_opposite_candles:
            return False, "opposite_candle_failed"
        return True, "ok"

    # Net mode intentionally ignores candle-by-candle colour. The expansion
    # must finish outside the base in the intended direction; magnitude is
    # checked separately using the strongest closing excursion in the move.
    end_close = float(move[-1]["close"])
    if direction == "bull" and end_close <= base_high:
        return False, "net_direction_failed"
    if direction == "bear" and end_close >= base_low:
        return False, "net_direction_failed"
    if directional < config.expansion_min_directional_candles:
        return False, "directional_candle_count_failed"
    if opposite and not config.expansion_allow_opposite_candles:
        return False, "opposite_candle_failed"
    return True, "ok"


def _expansion_size(move: list[dict[str, Any]], base_low: float, base_high: float, direction: str) -> float:
    """Measure the strongest closing excursion away from the base boundary."""
    if direction == "bull":
        return max(float(x["close"]) for x in move) - base_high
    return base_low - min(float(x["close"]) for x in move)


def _make_zone(
    *,
    base: list[dict[str, Any]],
    move: list[dict[str, Any]],
    config: DemandSupplyConfig,
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
        "base_quality": max(0.0, 1.0 - base_range / (config.base_range_atr_multiplier * float(atr_value))),
        "structure": 1.0,
        "created_at": move[-1]["date"],
        "expansion_candles": len(move),
    }


def _diag(
    *,
    timeframe: Timeframe,
    candidate_date: Any,
    base: list[dict[str, Any]] | None,
    status: str,
    reason: str,
    direction: str | None = None,
    move: list[dict[str, Any]] | None = None,
    atr_value: float | None = None,
    displacement_atr: float | None = None,
    volume_ratio: float | None = None,
) -> dict[str, Any]:
    return {
        "timeframe": timeframe.value,
        "date": candidate_date,
        "direction": direction,
        "status": status,
        "reason": reason,
        "base_start_date": base[0]["date"] if base else None,
        "base_end_date": base[-1]["date"] if base else None,
        "base_candles": len(base) if base else 0,
        "base_range": (max(x["high"] for x in base) - min(x["low"] for x in base)) if base else None,
        "atr": float(atr_value) if atr_value is not None else None,
        "displacement_atr": displacement_atr,
        "volume_ratio": volume_ratio,
        "expansion_candles": len(move) if move else 0,
        "displacement_date": move[-1]["date"] if move else None,
    }


def _prepare(candles: pl.DataFrame, as_of_date: date, config: DemandSupplyConfig) -> list[dict[str, Any]]:
    visible = candles.filter(pl.col("date") <= as_of_date).sort("date")
    if visible.height <= config.atr_period:
        return []
    return candle_metrics(atr(visible, config.atr_period)).sort("date").to_dicts()


def diagnose_zones(
    candles: pl.DataFrame,
    timeframe: Timeframe,
    config: DemandSupplyConfig,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Return candidate diagnostics and rejection reasons without lookahead."""
    if config.timeframe != timeframe:
        raise ValueError("config timeframe does not match requested timeframe")
    if candles.is_empty():
        return []
    rows = _prepare(candles, as_of_date, config)
    diagnostics: list[dict[str, Any]] = []
    for i in range(1, len(rows)):
        row = rows[i]
        atr_value = row.get("atr")
        if atr_value is None or atr_value <= 0:
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=None, status="rejected", reason="atr_unavailable"))
            continue
        candidates = _base_candidates(rows, i, config)
        if not candidates:
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=None, status="rejected", reason="no_valid_base", atr_value=atr_value))
            continue
        base = candidates[-1]
        direction = "bull" if row["close"] > row["open"] else "bear" if row["close"] < row["open"] else None
        if direction is None:
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="rejected", reason="doji_displacement", atr_value=atr_value))
            continue

        if config.detection_mode == "displacement":
            move = [row]
            base_low = min(x["low"] for x in base)
            base_high = max(x["high"] for x in base)
            move_size = row["close"] - base_low if direction == "bull" else base_high - row["close"]
            displacement_atr = move_size / float(atr_value)
            if displacement_atr < config.minimum_displacement_atr:
                diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="rejected", reason="displacement_below_threshold", direction=direction, move=move, atr_value=atr_value, displacement_atr=displacement_atr))
                continue
            volume_ratio = _volume_ratio(base, move)
            if config.volume_enabled and (volume_ratio is None or volume_ratio < config.minimum_volume_ratio):
                diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="rejected", reason="volume_below_threshold", direction=direction, move=move, atr_value=atr_value, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
                continue
            if config.structure_confirmation_required and not _structure_ok(rows, i, direction, row["close"]):
                diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="rejected", reason="structure_not_confirmed", direction=direction, move=move, atr_value=atr_value, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
                continue
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="accepted", reason="zone_detected", direction=direction, move=move, atr_value=atr_value, displacement_atr=displacement_atr, volume_ratio=volume_ratio))
            continue

        accepted = False
        last_reason = "expansion_not_found"
        last_expansion_atr: float | None = None
        last_move: list[dict[str, Any]] | None = None
        base_low = min(x["low"] for x in base)
        base_high = max(x["high"] for x in base)
        for length in range(1, min(config.expansion_max_candles, len(rows) - i) + 1):
            move = rows[i:i + length]
            last_move = move
            direction_ok, direction_reason = _expansion_direction_check(move, direction, base_low, base_high, config)
            if not direction_ok:
                last_reason = direction_reason
                continue
            end = move[-1]
            expansion_size = _expansion_size(move, base_low, base_high, direction)
            # Use the ATR at the candidate/creation candle for the threshold.
            # Otherwise a later ATR spike can change whether the same expansion
            # qualifies, making the threshold depend on an unrelated later bar.
            expansion_atr = expansion_size / float(atr_value)
            last_expansion_atr = expansion_atr
            if expansion_atr < config.minimum_expansion_atr:
                last_reason = "expansion_magnitude_failed"
                continue
            volume_ratio = _volume_ratio(base, move)
            if config.volume_enabled and (volume_ratio is None or volume_ratio < config.minimum_volume_ratio):
                last_reason = "volume_failed"
                continue
            if config.structure_confirmation_required and not _structure_ok(rows, i + length - 1, direction, end["close"]):
                last_reason = "structure_not_confirmed"
                continue
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="accepted", reason="zone_detected", direction=direction, move=move, atr_value=atr_value, displacement_atr=expansion_atr, volume_ratio=volume_ratio))
            accepted = True
            break
        if not accepted:
            diagnostics.append(_diag(timeframe=timeframe, candidate_date=row["date"], base=base, status="rejected", reason=last_reason, direction=direction, move=last_move if last_reason == "expansion_magnitude_failed" else None, atr_value=atr_value, displacement_atr=last_expansion_atr))
    return diagnostics


def _zones_from_diagnostics(
    rows: list[dict[str, Any]],
    diagnostics: list[dict[str, Any]],
    config: DemandSupplyConfig,
) -> list[dict[str, Any]]:
    """Materialize exactly the candidates accepted by ``diagnose_zones``."""
    zones: list[dict[str, Any]] = []
    for item in diagnostics:
        if item["status"] != "accepted":
            continue
        base_start = item["base_start_date"]
        base_end = item["base_end_date"]
        move_end = item["displacement_date"]
        base = [x for x in rows if base_start <= x["date"] <= base_end]
        move = [x for x in rows if x["date"] > base_end and x["date"] <= move_end]
        expected = max(1, int(item["expansion_candles"] or 0))
        if not base or not move or len(move) != expected:
            raise RuntimeError(
                "zone diagnostic/materialization mismatch: "
                f"base={base_start}..{base_end}, move_end={move_end}, "
                f"expected_move={expected}, actual_move={len(move)}"
            )
        zones.append(
            _make_zone(
                base=base,
                move=move,
                config=config,
                atr_value=float(item["atr"]),
                direction=item["direction"],
                displacement_atr=float(item["displacement_atr"]),
                volume_ratio=item["volume_ratio"],
            )
        )
    return zones


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
    rows = _prepare(candles, as_of_date, config)
    diagnostics = diagnose_zones(candles, timeframe, config, as_of_date)
    return _zones_from_diagnostics(rows, diagnostics, config)
