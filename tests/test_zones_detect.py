from datetime import date, timedelta

import polars as pl

from algotrade.zones.config import DemandSupplyConfig, Timeframe
from algotrade.zones.detect import detect_zones


def test_detection_never_sees_rows_after_cutoff():
    start = date(2024, 1, 1)
    rows = []
    for i in range(30):
        rows.append({"date": start + timedelta(days=i), "symbol": "ABC", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.1, "volume": 100.0})
    rows[-1].update({"open": 10.0, "high": 14.0, "low": 9.5, "close": 13.5, "volume": 500.0})
    candles = pl.DataFrame(rows)
    config = DemandSupplyConfig(Timeframe.DAILY, atr_period=5, min_sessions_history=1, minimum_displacement_atr=1.0)
    assert detect_zones(candles, Timeframe.DAILY, config, start + timedelta(days=27)) == detect_zones(candles.head(28), Timeframe.DAILY, config, start + timedelta(days=27))
