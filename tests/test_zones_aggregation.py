from datetime import date, timedelta

import polars as pl

from algotrade.zones.aggregation import aggregate_to_monthly, aggregate_to_weekly, split_completed_current
from algotrade.zones.config import Timeframe


def _candles(days=35):
    start = date(2024, 1, 1)
    return pl.DataFrame([{"date": start + timedelta(days=i), "symbol": "ABC", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0} for i in range(days)])


def test_weekly_and_monthly_are_ohlcv_aggregations():
    daily = _candles()
    weekly = aggregate_to_weekly(daily)
    monthly = aggregate_to_monthly(daily)
    assert weekly.height >= 5
    assert monthly.height == 2
    assert monthly["volume"].sum() == daily["volume"].sum()


def test_completed_current_split_does_not_use_future_rows():
    weekly = aggregate_to_weekly(_candles())
    completed, current = split_completed_current(weekly, date(2024, 1, 20), Timeframe.WEEKLY)
    assert current is not None
    assert completed["date"].max() < date(2024, 1, 20)
