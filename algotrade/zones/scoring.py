from __future__ import annotations

from datetime import date

from .config import DemandSupplyConfig


def score_zone(
    zone,
    *,
    config: DemandSupplyConfig,
    as_of_date: date | None = None,
) -> int:
    """Return the configured 0-100 zone strength score."""
    displacement = min(
        1.0,
        max(0.0, float(zone.displacement_atr) / max(config.minimum_displacement_atr, 1e-9) / 2.0),
    )
    base_quality = min(1.0, max(0.0, float(getattr(zone, "base_quality", 0.5))))
    volume = min(1.0, max(0.0, float(zone.volume_ratio or 1.0) / 2.0))
    structure = min(1.0, max(0.0, float(getattr(zone, "structure_score", 1.0))))
    reference_date = as_of_date or zone.created_at
    age = max(0, (reference_date - zone.created_at).days)
    freshness = 1.0 / (1.0 + 0.05 * age + 0.25 * zone.touch_count)
    weighted = (
        config.displacement_weight * displacement
        + config.base_quality_weight * base_quality
        + config.volume_weight * volume
        + config.structure_weight * structure
        + config.freshness_weight * freshness
    )
    return int(round(max(0.0, min(100.0, weighted))))
