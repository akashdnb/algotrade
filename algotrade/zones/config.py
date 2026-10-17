from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml


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


@dataclass(frozen=True, slots=True)
class DemandSupplyConfig:
    timeframe: Timeframe
    base_max_candles: int = 3
    base_range_atr_multiplier: float = 0.75
    minimum_displacement_atr: float = 1.5
    atr_period: int = 14
    volume_enabled: bool = True
    minimum_volume_ratio: float = 1.0
    structure_confirmation_required: bool = False
    invalidation_mode: Literal["close", "wick"] = "close"
    invalidation_buffer_atr: float = 0.0
    approaching_threshold_pct: float = 5.0
    near_threshold_pct: float = 2.0
    displacement_weight: int = 30
    base_quality_weight: int = 20
    volume_weight: int = 15
    structure_weight: int = 15
    freshness_weight: int = 20
    min_price: float = 5.0
    min_sessions_history: int = 400
    minimum_body_ratio: float = 0.05

    def __post_init__(self) -> None:
        if self.base_max_candles < 1 or self.atr_period < 1 or self.min_sessions_history < 1:
            raise ValueError("candle, ATR and history periods must be positive")
        if sum((self.displacement_weight, self.base_quality_weight, self.volume_weight,
                self.structure_weight, self.freshness_weight)) != 100:
            raise ValueError("zone scoring weights must sum to 100")
        if self.invalidation_mode not in ("close", "wick"):
            raise ValueError("invalidation_mode must be close or wick")
        if self.near_threshold_pct < 0 or self.approaching_threshold_pct < self.near_threshold_pct:
            raise ValueError("proximity thresholds must be non-negative and ordered")


def load_configs(path: Path) -> dict[Timeframe, DemandSupplyConfig]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    rows = raw.get("timeframes", raw)
    result: dict[Timeframe, DemandSupplyConfig] = {}
    for key, values in rows.items():
        tf = Timeframe(str(key).lower())
        result[tf] = DemandSupplyConfig(timeframe=tf, **(values or {}))
    missing = set(Timeframe) - set(result)
    if missing:
        raise ValueError(f"missing timeframe configs: {sorted(x.value for x in missing)}")
    return result


__all__ = ["DemandSupplyConfig", "Timeframe", "ZoneType", "ZoneStatus", "ZoneProximity"]
