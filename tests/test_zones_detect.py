from datetime import date, timedelta

import polars as pl

from algotrade.zones.config import DemandSupplyConfig, Timeframe
from algotrade.zones.detect import _expansion_direction_check, detect_zones


def test_detection_never_sees_rows_after_cutoff():
    start = date(2024, 1, 1)
    rows = []
    for i in range(30):
        rows.append({"date": start + timedelta(days=i), "symbol": "ABC", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.1, "volume": 100.0})
    rows[-1].update({"open": 10.0, "high": 14.0, "low": 9.5, "close": 13.5, "volume": 500.0})
    candles = pl.DataFrame(rows)
    config = DemandSupplyConfig(Timeframe.DAILY, atr_period=5, min_sessions_history=1, minimum_displacement_atr=1.0)
    assert detect_zones(candles, Timeframe.DAILY, config, start + timedelta(days=27)) == detect_zones(candles.head(28), Timeframe.DAILY, config, start + timedelta(days=27))


def test_expansion_direction_modes_allow_mixed_net_move():
    move = [
        {"open": 100.0, "close": 108.0},
        {"open": 108.0, "close": 104.0},
        {"open": 104.0, "close": 116.0},
    ]

    strict = DemandSupplyConfig(Timeframe.MONTHLY, detection_mode="expansion", expansion_direction_mode="strict", expansion_max_candles=3, min_sessions_history=1)
    majority = DemandSupplyConfig(Timeframe.MONTHLY, detection_mode="expansion", expansion_direction_mode="majority", expansion_max_candles=3, min_sessions_history=1)
    net = DemandSupplyConfig(Timeframe.MONTHLY, detection_mode="expansion", expansion_direction_mode="net", expansion_max_candles=3, min_sessions_history=1, expansion_min_directional_candles=1, expansion_allow_opposite_candles=True)

    assert _expansion_direction_check(move, "bull", strict) == (False, "directional_candle_count_failed")
    assert _expansion_direction_check(move, "bull", majority) == (True, "ok")
    assert _expansion_direction_check(move, "bull", net) == (True, "ok")
