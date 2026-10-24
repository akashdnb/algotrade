from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from algotrade.domain.zones import Timeframe, ZoneProximity, ZoneStatus, ZoneType


DetectionMode = Literal["displacement", "expansion"]
ExpansionDirectionMode = Literal["strict", "majority", "net"]


@dataclass(frozen=True, slots=True)
class DemandSupplyConfig:
    timeframe: Timeframe
    detection_mode: DetectionMode = "displacement"
    base_max_candles: int = 3
    base_range_atr_multiplier: float = 0.75
    minimum_displacement_atr: float = 1.5
    expansion_max_candles: int = 3
    minimum_expansion_atr: float = 1.5
    expansion_direction_mode: ExpansionDirectionMode = "strict"
    expansion_min_directional_candles: int = 1
    expansion_allow_opposite_candles: bool = False
    atr_period: int = 14
    volume_enabled: bool = True
    minimum_volume_ratio: float = 1.0
    structure_confirmation_required: bool = False
    invalidation_mode: Literal["close", "wick"] = "close"
    invalidation_buffer_atr: float = 0.0
    approaching_threshold_pct: float = 5.0
    near_threshold_pct: float = 2.0
    displacement_weight: int = 35
    base_quality_weight: int = 25
    volume_weight: int = 20
    #: Zero because detection never computes structure (every zone carries a
    #: constant 1.0), so a non-zero weight only adds free points.
    structure_weight: int = 0
    freshness_weight: int = 20
    min_price: float = 5.0
    #: Trailing 20-session median turnover floor (Rs), reusing the momentum
    #: universe's proven liquidity bar (`core.universe.MIN_TURNOVER`) rather
    #: than inventing a zones-specific one. Thin names let `_volume_ratio`'s
    #: near-zero denominator produce meaningless ratios in the thousands,
    #: which saturate the volume score the same as a genuine 2x and were
    #: found concentrated in the highest-scored, highest-failure-rate zones.
    min_turnover: float = 5e7
    #: Fraction of the lookback window the symbol must have actually traded
    #: in, matching `core.universe.MIN_COVERAGE`; excludes sporadic traders
    #: a turnover floor alone would miss.
    min_coverage: float = 0.80
    min_sessions_history: int = 400
    minimum_body_ratio: float = 0.05
    #: A zone older than this many bars of its own timeframe is dropped from the
    #: output. Hygiene, not alpha: the entry study found no age at which zones
    #: start working, but a 16-year-old level is not an actionable one. None
    #: keeps every zone.
    max_age_bars: int | None = None
    #: Non-broken zones of one symbol/timeframe/direction whose price ranges
    #: overlap by more than this intersection-over-union are one level; only the
    #: strongest is kept. None disables.
    dedupe_overlap: float | None = 0.5

    def __post_init__(self) -> None:
        if self.detection_mode not in ("displacement", "expansion"):
            raise ValueError("detection_mode must be displacement or expansion")
        if self.expansion_direction_mode not in ("strict", "majority", "net"):
            raise ValueError("expansion_direction_mode must be strict, majority or net")
        if self.base_max_candles < 1 or self.atr_period < 1 or self.expansion_max_candles < 1 or self.min_sessions_history < 1:
            raise ValueError("candle, ATR and history periods must be positive")
        if self.expansion_min_directional_candles < 1 or self.expansion_min_directional_candles > self.expansion_max_candles:
            raise ValueError("expansion_min_directional_candles must be between 1 and expansion_max_candles")
        if self.base_range_atr_multiplier <= 0:
            raise ValueError("base_range_atr_multiplier must be positive")
        if self.minimum_displacement_atr <= 0 or self.minimum_expansion_atr <= 0:
            raise ValueError("displacement and expansion thresholds must be positive")
        if self.minimum_body_ratio < 0 or self.minimum_body_ratio > 1:
            raise ValueError("minimum_body_ratio must be between 0 and 1")
        if self.minimum_volume_ratio < 0:
            raise ValueError("minimum_volume_ratio must be non-negative")
        if sum((self.displacement_weight, self.base_quality_weight, self.volume_weight, self.structure_weight, self.freshness_weight)) != 100:
            raise ValueError("zone scoring weights must sum to 100")
        if self.invalidation_mode not in ("close", "wick"):
            raise ValueError("invalidation_mode must be close or wick")
        if self.near_threshold_pct < 0 or self.approaching_threshold_pct < self.near_threshold_pct:
            raise ValueError("proximity thresholds must be non-negative and ordered")
        if self.max_age_bars is not None and self.max_age_bars < 1:
            raise ValueError("max_age_bars must be positive")
        if self.dedupe_overlap is not None and not 0 < self.dedupe_overlap <= 1:
            raise ValueError("dedupe_overlap must be in (0, 1]")
        if self.min_turnover < 0:
            raise ValueError("min_turnover must be non-negative")
        if not 0 <= self.min_coverage <= 1:
            raise ValueError("min_coverage must be between 0 and 1")


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


__all__ = ["DemandSupplyConfig", "DetectionMode", "ExpansionDirectionMode", "Timeframe", "ZoneType", "ZoneStatus", "ZoneProximity"]
