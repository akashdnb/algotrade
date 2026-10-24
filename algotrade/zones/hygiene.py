"""Output hygiene for zones: expiry and de-duplication.

Neither changes how a zone forms. Both decide which formed zones are still
worth showing, so they live apart from detection and are applied by the
pipeline after lifecycle and scoring.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from .config import ZoneStatus


def _iou(a, b) -> Decimal:
    inter = min(a.zone_high, b.zone_high) - max(a.zone_low, b.zone_low)
    if inter < 0:
        return Decimal(0)
    union = max(a.zone_high, b.zone_high) - min(a.zone_low, b.zone_low)
    return inter / union if union else Decimal(1)


def drop_expired(zones: Iterable, ages: dict[str, int], max_age_bars: int | None) -> list:
    """Zones no older than `max_age_bars` (all of them when None). `ages` maps zone_id to bars since creation."""
    zones = list(zones)
    if max_age_bars is None:
        return zones
    return [z for z in zones if ages.get(z.zone_id, 0) <= max_age_bars]


def dedupe_zones(zones: Iterable, overlap: float | None) -> list:
    """Collapse overlapping live zones of one symbol/timeframe/direction to the strongest.

    Ties go to the more recent zone. Broken zones are history, not levels, and
    pass through untouched. Output order follows the input.
    """
    zones = list(zones)
    if overlap is None:
        return zones
    threshold = Decimal(str(overlap))
    ranked = sorted(
        (z for z in zones if z.status != ZoneStatus.BROKEN),
        key=lambda z: (-z.strength_score, -z.created_at.toordinal(), z.zone_id),
    )
    kept: list = []
    for zone in ranked:
        if not any(k.symbol == zone.symbol and k.timeframe == zone.timeframe and k.zone_type == zone.zone_type and _iou(k, zone) > threshold for k in kept):
            kept.append(zone)
    keep_ids = {z.zone_id for z in kept}
    return [z for z in zones if z.status == ZoneStatus.BROKEN or z.zone_id in keep_ids]
