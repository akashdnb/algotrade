from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal
from .config import Timeframe, ZoneStatus, ZoneType

@dataclass(frozen=True, slots=True)
class Confluence:
    symbol: str
    zone_type: ZoneType
    timeframes: tuple[Timeframe, ...]
    overlap_low: Decimal
    overlap_high: Decimal
    distance_pct: float


def calculate_confluence(zones, current_price: Decimal):
    grouped = {}
    for zone in zones:
        if zone.status != ZoneStatus.BROKEN:
            grouped.setdefault((zone.symbol, zone.zone_type), []).append(zone)
    out = []
    for (symbol, kind), items in grouped.items():
        best = None
        for i, left in enumerate(items):
            group = [left]
            low, high = left.zone_low, left.zone_high
            seen = {left.timeframe}
            for right in items[i + 1:]:
                if right.timeframe in seen:
                    continue
                lo, hi = max(low, right.zone_low), min(high, right.zone_high)
                if lo <= hi:
                    group.append(right); seen.add(right.timeframe); low, high = lo, hi
            if len(group) >= 2 and (best is None or len(group) > len(best[0])):
                best = (group, low, high)
        if best:
            group, low, high = best
            pct = 0.0 if low <= current_price <= high else float(min(abs(current_price-low), abs(current_price-high)) / current_price * 100)
            out.append(Confluence(symbol, kind, tuple(sorted((z.timeframe for z in group), key=lambda x: list(Timeframe).index(x))), low, high, pct))
    return out
