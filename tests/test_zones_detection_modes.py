from datetime import date, timedelta

import polars as pl

from algotrade.zones.config import DemandSupplyConfig, Timeframe
from algotrade.zones.detect import detect_zones, diagnose_zones


def _monthly_candles() -> pl.DataFrame:
    start = date(2024, 1, 1)
    rows = []
    close = 100.0
    for i in range(14):
        d = start + timedelta(days=30 * i)
        rows.append({"symbol": "TEST", "date": d, "open": close, "high": close + 4, "low": close - 4, "close": close + 1, "volume": 100.0})
        close += 1
    # Tight base followed by a two-candle bullish expansion.
    for j, (o, c) in enumerate(((101.0, 101.5), (101.2, 101.8))):
        d = start + timedelta(days=30 * (14 + j))
        rows.append({"symbol": "TEST", "date": d, "open": o, "high": c + 1, "low": o - 1, "close": c, "volume": 180.0})
    d = start + timedelta(days=30 * 16)
    rows.append({"symbol": "TEST", "date": d, "open": 101.8, "high": 112.0, "low": 101.5, "close": 111.0, "volume": 350.0})
    return pl.DataFrame(rows)


def _config(mode: str) -> DemandSupplyConfig:
    return DemandSupplyConfig(
        timeframe=Timeframe.MONTHLY,
        detection_mode=mode,
        base_max_candles=2,
        base_range_atr_multiplier=1.5,
        minimum_displacement_atr=1.5,
        expansion_max_candles=3,
        minimum_expansion_atr=1.2,
        atr_period=6,
        volume_enabled=True,
        minimum_volume_ratio=1.0,
    )


def test_expansion_mode_can_detect_multi_candle_demand() -> None:
    zones = detect_zones(_monthly_candles(), Timeframe.MONTHLY, _config("expansion"), date(2025, 6, 1))
    assert any(z["zone_type"] == "demand" and z["expansion_candles"] >= 2 for z in zones)


def test_diagnostics_explain_rejections() -> None:
    diagnostics = diagnose_zones(_monthly_candles(), Timeframe.MONTHLY, _config("displacement"), date(2025, 6, 1))
    assert diagnostics
    assert all({"status", "reason", "base_candles", "base_range", "atr"}.issubset(item) for item in diagnostics)
