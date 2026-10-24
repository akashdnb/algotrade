from __future__ import annotations

import math
from datetime import date

from .config import DemandSupplyConfig

#: Volume ratio at which the volume term saturates. Log-scaled so 2x and 8x are
#: distinguishable; the previous linear term saturated at 2x, which the median
#: zone already exceeds, and a near-zero base volume could push it to 16,000x.
VOLUME_RATIO_CAP = 8.0


def _volume_term(ratio: float | None) -> float:
    if ratio is None or ratio <= 1.0:
        return 0.0
    return min(1.0, math.log(ratio) / math.log(VOLUME_RATIO_CAP))


def score_zone(
    zone,
    *,
    config: DemandSupplyConfig,
    as_of_date: date | None = None,
    age_bars: int | None = None,
) -> int:
    """Return the configured 0-100 zone cleanliness score.

    Describes how clean the formation was (displacement, compact base, volume)
    and how fresh it is. The entry study found no evidence that it predicts
    returns, so it ranks zones for display and must not be read as a forecast.

    Args:
        age_bars: Bars of the zone's own timeframe since creation. Calendar days
            are used when omitted, which over-ages weekly and monthly zones.
    """
    displacement = min(
        1.0,
        max(0.0, float(zone.displacement_atr) / max(config.minimum_displacement_atr, 1e-9) / 2.0),
    )
    base_quality = min(1.0, max(0.0, float(getattr(zone, "base_quality", 0.5))))
    volume = _volume_term(zone.volume_ratio)
    structure = min(1.0, max(0.0, float(getattr(zone, "structure_score", 1.0))))
    if age_bars is None:
        reference_date = as_of_date or zone.created_at
        age_bars = max(0, (reference_date - zone.created_at).days)
    freshness = 1.0 / (1.0 + 0.05 * age_bars + 0.25 * zone.touch_count)
    weighted = (
        config.displacement_weight * displacement
        + config.base_quality_weight * base_quality
        + config.volume_weight * volume
        + config.structure_weight * structure
        + config.freshness_weight * freshness
    )
    return int(round(max(0.0, min(100.0, weighted))))
