from datetime import date, timedelta
from decimal import Decimal

import polars as pl

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.backtest import event_study_metrics


def test_event_study_reports_creation_move_and_slices():
    zone = Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1"), 2.0, 2.0, 80, ZoneStatus.TESTED, 1, date(2024,1,5), date(2024,1,5), date(2024,1,3), None)
    panel = pl.DataFrame([{"date": date(2024,1,4) + timedelta(days=i), "symbol": "ABC", "open": 10.0, "high": 11.0 + i, "low": 9.8, "close": 10.5 + i * .1, "volume": 100.0} for i in range(6)])
    result = event_study_metrics([zone], panel)
    assert result["overall"]["zones"] == 1
    assert result["overall"]["tested"] == 1
    assert "timeframe" in result["slices"]
