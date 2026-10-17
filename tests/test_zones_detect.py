from datetime import date, timedelta

import polars as pl

from algotrade.zones.config import DemandSupplyConfig, Timeframe
from algotrade.zones.detect import _expansion_size, detect_zones


def test_detection_never_sees_rows_after_cutoff():
    start = date(2024, 1, 1)
    rows = []
    for i in range(30):
        rows.append({"date": start + timedelta(days=i), "symbol": "ABC", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.1, "volume": 100.0})
    rows[-1].update({"open": 10.0, "high": 14.0, "low": 9.5, "close": 13.5, "volume": 500.0})
    candles = pl.DataFrame(rows)
    config = DemandSupplyConfig(Timeframe.DAILY, atr_period=5, min_sessions_history=1, minimum_displacement_atr=1.0)
    assert detect_zones(candles, Timeframe.DAILY, config, start + timedelta(days=27)) == detect_zones(candles.head(28), Timeframe.DAILY, config, start + timedelta(days=27))


def test_expansion_net_mode_accepts_mixed_candles():
    start = date(2025, 1, 1)
    rows = []
    for i in range(10):
        rows.append({
            "date": start + timedelta(days=i),
            "symbol": "ABC",
            "open": 100.0,
            "high": 102.0,
            "low": 98.0,
            "close": 100.0,
            "volume": 100.0,
        })
    rows[-3:] = [
        {"date": start + timedelta(days=7), "symbol": "ABC", "open": 100.0, "high": 108.0, "low": 99.0, "close": 107.0, "volume": 200.0},
        {"date": start + timedelta(days=8), "symbol": "ABC", "open": 107.0, "high": 108.0, "low": 102.0, "close": 103.0, "volume": 200.0},
        {"date": start + timedelta(days=9), "symbol": "ABC", "open": 103.0, "high": 120.0, "low": 102.0, "close": 118.0, "volume": 250.0},
    ]
    candles = pl.DataFrame(rows)
    strict = DemandSupplyConfig(Timeframe.MONTHLY, detection_mode="expansion", expansion_direction_mode="strict", expansion_max_candles=3, atr_period=3, min_sessions_history=1, minimum_expansion_atr=1.0)
    net = DemandSupplyConfig(Timeframe.MONTHLY, detection_mode="expansion", expansion_direction_mode="net", expansion_max_candles=3, atr_period=3, min_sessions_history=1, minimum_expansion_atr=1.0, expansion_min_directional_candles=1, expansion_allow_opposite_candles=True)

    strict_zones = detect_zones(candles, Timeframe.MONTHLY, strict, start + timedelta(days=9))
    net_zones = detect_zones(candles, Timeframe.MONTHLY, net, start + timedelta(days=9))

    assert not any(zone["zone_type"] == "demand" for zone in strict_zones)
    assert any(zone["zone_type"] == "demand" for zone in net_zones)


def test_expansion_uses_strongest_close_not_only_final_close():
    move = [
        {"close": 140.0},
        {"close": 115.0},
        {"close": 112.0},
    ]
    # The expansion is measured from the upper base boundary. A later
    # retracement must not erase a valid earlier closing expansion.
    assert _expansion_size(move, 90.0, 110.0, "bull") == 30.0
    assert _expansion_size(move, 90.0, 110.0, "bear") == -2.0
