from __future__

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum


class Timeframe(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class ZoneType(StrEnum):
    DEMAND = "demand"
    SUPPLY = "supply"


class ZoneStatus(StrEnum):
    ACTIVE = "active"
    TESTED = "tested"
    BROKEN = "broken"


class ZoneProximity(StrEnum):
    FAR = "far"
    APPROACHING = "approaching"
    NEAR = "near"
    IN_ZONE = "in_zone"
    BROKEN = "broken"


ZoneTimeframe = Timeframe


@dataclass(frozen=True, slots=True)
class Zone:
    symbol: str
    timeframe: Timeframe
    zone_type: ZoneType
    zone_low: Decimal
    zone_high: Decimal
    base_start_date: date
    base_end_date: date
    displacement_date: date
    atr_at_creation: Decimal
    displacement_atr: float
    volume_ratio: float | None
    strength_score: int
    status: ZoneStatus
    touch_count: int
    first_tested_at: date | None
    last_tested_at: date | None
    created_at: date
    invalidated_at: date | None
    base_quality: float = 0.5
    structure_score: float = 1.0

    @property
    def zone_id(self) -> str:
        return (
            f"{self.symbol}:{self.timeframe.value}:{self.zone_type.value}:"
            f"{self.base_start_date.isoformat()}:{self.base_end_date.isoformat()}:"
            f"{self.displacement_date.isoformat()}"
        )


__all__ = ["Zone", "Timeframe", "ZoneTimeframe", "ZoneType", "ZoneStatus", "ZoneProximity"]
