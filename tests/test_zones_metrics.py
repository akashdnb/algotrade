from datetime import date, timedelta

import polars as pl

from algotrade.zones.metrics import atr, candle_metrics


def _candles():
    return pl.DataFrame([{"date": date(2024, 1, 1) + timedelta(days=i), "symbol": "ABC", "open": 10.0 + i, "high": 12.0 + i, "low": 9.0 + i, "close": 11.0 + i, "volume": 100.0} for i in range(5)])


def test_candle_metrics_and_atr_are_defined_after_seed_period():
    candles = _candles()
    metrics = candle_metrics(candles)
    result = atr(candles, 3)
    assert metrics["body_ratio"][0] == 1 / 3
    assert result["atr"][1] is None
    assert result["atr"][2] is not None
