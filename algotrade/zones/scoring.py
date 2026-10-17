from __future__ import annotations

from datetime import date

from .config import DemandSupplyConfig


def score_zone(zone, *, config: DemandSupplyConfig) -> int:
    displacement = min(1.0, max(0.0, float(zone.displacement_atr) / max(config.minimum_displacement_atr, 1e-9) / 2.0))
    base_quality = min(1.0, max(0.0, getattr(zone, "base_quality", 0.5)))
    volume = min(1.0, max(0.0, float(zone.volume_ratio or 1.0) / 2.0))
    structure = min(1.0, max(0.0, getattr(zone, "structure_score", 1.0)))
    age = max(0, (zone.created_at - zone.base_start_date).days)
    freshness = 1.0 / (1.0 + 0.05 * age + 0.25 * zone.touch_count)
    return int(round(100 * (
        config.displacement_weight * displacement +
        config.base_quality_weight * base_quality +
        config.volume_weight * volume +
        config.structure_weight * structure +
        config.freshness_weight * freshness
    ) / 100))
