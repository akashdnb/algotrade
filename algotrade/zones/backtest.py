from __future__ import annotations

from collections import defaultdict
from statistics import mean

import polars as pl


def _summary(rows: list[dict], horizons: tuple[int, ...]) -> dict:
    if not rows:
        return {"zones": 0, "tested": 0, "broken": 0, "failure_rate": 0.0}
    tested = sum(bool(row["tested"]) for row in rows)
    broken = sum(bool(row["broken"]) for row in rows)
    out = {
        "zones": len(rows),
        "fresh": sum(not row["tested"] for row in rows),
        "tested": tested,
        "broken": broken,
        "failure_rate": broken / tested if tested else 0.0,
        "average_move_after_creation": mean(row["move_after_creation"] for row in rows),
        "average_reaction_after_first_test": (
            mean(row["reaction"] for row in rows if row["reaction"] is not None)
            if any(row["reaction"] is not None for row in rows) else None
        ),
    }
    for horizon in horizons:
        values = [row[f"mfe_{horizon}"] for row in rows if row[f"mfe_{horizon}"] is not None]
        mae = [row[f"mae_{horizon}"] for row in rows if row[f"mae_{horizon}"] is not None]
        out[f"average_mfe_{horizon}_sessions"] = mean(values) if values else None
        out[f"average_mae_{horizon}_sessions"] = mean(mae) if mae else None
    return out


def _favourable(zone, price: float) -> float:
    if zone.zone_type.value == "demand":
        return (price - float(zone.zone_high)) / max(float(zone.zone_high), 1e-9)
    return (float(zone.zone_low) - price) / max(float(zone.zone_low), 1e-9)


def _adverse(zone, price: float) -> float:
    if zone.zone_type.value == "demand":
        return (float(zone.zone_low) - price) / max(float(zone.zone_low), 1e-9)
    return (price - float(zone.zone_high)) / max(float(zone.zone_high), 1e-9)


def event_study_metrics(
    zones,
    panel: pl.DataFrame,
    *,
    horizons: tuple[int, ...] = (5, 10, 20),
    distance_threshold_pct: float | None = None,
    volume_threshold: float | None = None,
) -> dict:
    """Pure event-study metrics; no I/O and no mutation of zones or panel."""
    rows: list[dict] = []
    for zone in zones:
        bars = (
            panel.filter((pl.col("symbol") == zone.symbol) & (pl.col("date") > zone.created_at))
            .sort("date")
            .to_dicts()
        )
        if not bars:
            continue
        first = bars[0]
        distance_pct = min(
            abs(float(first["close"]) - float(zone.zone_low)),
            abs(float(first["close"]) - float(zone.zone_high)),
        ) / max(float(first["close"]), 1e-9) * 100
        if distance_threshold_pct is not None and distance_pct > distance_threshold_pct:
            continue
        if volume_threshold is not None and (zone.volume_ratio or 0.0) < volume_threshold:
            continue

        row = {
            "timeframe": zone.timeframe.value,
            "score": zone.strength_score,
            "displacement_atr": zone.displacement_atr,
            "volume_ratio": zone.volume_ratio,
            "distance_pct": distance_pct,
            "tested": zone.touch_count > 0,
            "broken": zone.status.value == "broken",
            "move_after_creation": _favourable(zone, float(first["close"])),
            "reaction": None,
        }
        if zone.first_tested_at:
            tested = [bar for bar in bars if bar["date"] >= zone.first_tested_at]
            if tested:
                row["reaction"] = max(_favourable(zone, float(bar["close"])) for bar in tested[:20])

        for horizon in horizons:
            future = bars[:horizon]
            row[f"mfe_{horizon}"] = max((_favourable(zone, float(bar["high"] if zone.zone_type.value == "demand" else bar["low"])) for bar in future), default=None)
            row[f"mae_{horizon}"] = max((_adverse(zone, float(bar["low"] if zone.zone_type.value == "demand" else bar["high"])) for bar in future), default=None)
        rows.append(row)

    overall = _summary(rows, horizons)
    slices: dict[str, dict] = {}
    for key, selector in {
        "timeframe": lambda r: r["timeframe"],
        "score_bucket": lambda r: "<60" if r["score"] < 60 else "60-79" if r["score"] < 80 else "80+",
        "displacement_atr_bucket": lambda r: "<2" if r["displacement_atr"] < 2 else "2-3" if r["displacement_atr"] < 3 else "3+",
        "distance_bucket": lambda r: "<=2%" if r["distance_pct"] <= 2 else "2-5%" if r["distance_pct"] <= 5 else "5%+",
        "volume_bucket": lambda r: "<1.5x" if (r["volume_ratio"] or 0) < 1.5 else "1.5x+",
    }.items():
        grouped = defaultdict(list)
        for row in rows:
            grouped[selector(row)].append(row)
        slices[key] = {bucket: _summary(items, horizons) for bucket, items in sorted(grouped.items())}
    return {"overall": overall, "slices": slices}
