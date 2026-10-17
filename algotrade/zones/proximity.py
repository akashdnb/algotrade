from __future__ import annotations
from decimal import Decimal
from .config import DemandSupplyConfig, ZoneProximity, ZoneStatus, ZoneType


def calculate_zone_distance(zone, current_price: Decimal):
    if zone.zone_low <= current_price <= zone.zone_high:
        return Decimal("0"), 0.0
    distance = zone.zone_low - current_price if current_price < zone.zone_low else current_price - zone.zone_high
    return distance, float(abs(distance) / current_price * 100) if current_price else 0.0


def classify_zone_proximity(zone, current_price: Decimal, config: DemandSupplyConfig):
    if zone.status == ZoneStatus.BROKEN:
        return ZoneProximity.BROKEN
    if zone.zone_low <= current_price <= zone.zone_high:
        return ZoneProximity.IN_ZONE
    if zone.zone_type == ZoneType.DEMAND and current_price < zone.zone_low:
        return ZoneProximity.FAR
    if zone.zone_type == ZoneType.SUPPLY and current_price > zone.zone_high:
        return ZoneProximity.FAR
    _, pct = calculate_zone_distance(zone, current_price)
    if pct <= config.near_threshold_pct:
        return ZoneProximity.NEAR
    if pct <= config.approaching_threshold_pct:
        return ZoneProximity.APPROACHING
    return ZoneProximity.FAR
