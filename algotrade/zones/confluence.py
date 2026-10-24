from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from itertools import combinations, product

from .config import Timeframe, ZoneStatus, ZoneType


@dataclass(frozen=True, slots=True)
class Confluence:
    symbol: str
    zone_type: ZoneType
    timeframes: tuple[Timeframe, ...]
    overlap_low: Decimal
    overlap_high: Decimal
    distance_pct: float


def _intersection(items):
    low = max(zone.zone_low for zone in items)
    high = min(zone.zone_high for zone in items)
    return (low, high) if low <= high else None


def calculate_confluence(zones, current_price: Decimal):
    """Return every maximal overlapping timeframe cluster deterministically."""
    grouped: dict[tuple[str, ZoneType], list] = {}
    for zone in zones:
        if zone.status != ZoneStatus.BROKEN:
            grouped.setdefault((zone.symbol, zone.zone_type), []).append(zone)

    out: list[Confluence] = []
    order = {tf: i for i, tf in enumerate(Timeframe)}
    for (symbol, kind), items in grouped.items():
        # A valid cluster has at most one zone per timeframe, so build candidates
        # per distinct subset of timeframes (at most 3, hence at most 4 subsets)
        # rather than over every combination of the (unbounded) zone list -- the
        # latter is exponential in the zone count and does not finish for a
        # symbol that has accumulated a few dozen still-active zones.
        by_timeframe: dict[Timeframe, list] = {}
        for zone in items:
            by_timeframe.setdefault(zone.timeframe, []).append(zone)
        timeframes_present = list(by_timeframe)

        candidates = []
        for size in range(2, len(timeframes_present) + 1):
            for tf_combo in combinations(timeframes_present, size):
                for combo in product(*(by_timeframe[tf] for tf in tf_combo)):
                    overlap = _intersection(combo)
                    if overlap:
                        candidates.append((combo, overlap[0], overlap[1]))

        maximal = []
        for candidate in candidates:
            ids = {zone.zone_id for zone in candidate[0]}
            if any(ids < {zone.zone_id for zone in other[0]} for other in candidates):
                continue
            maximal.append(candidate)

        for combo, low, high in sorted(
            maximal,
            key=lambda item: (-len(item[0]), tuple(order[z.timeframe] for z in item[0]), tuple(z.zone_id for z in item[0])),
        ):
            pct = 0.0 if low <= current_price <= high else (
                float(min(abs(current_price - low), abs(current_price - high)) / current_price * 100)
                if current_price else 0.0
            )
            out.append(
                Confluence(
                    symbol,
                    kind,
                    tuple(sorted((z.timeframe for z in combo), key=order.get)),
                    low,
                    high,
                    pct,
                )
            )
    return out
