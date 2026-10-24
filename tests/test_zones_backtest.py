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


def _panel(closes, lows=None, highs=None):
    return pl.DataFrame([
        {"date": date(2024, 1, 4) + timedelta(days=i), "symbol": "ABC", "open": c, "high": (highs or closes)[i] + 0.1, "low": (lows or closes)[i] - 0.1, "close": c, "volume": 100.0}
        for i, c in enumerate(closes)
    ])


def _demand():
    return Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), Decimal("1"), 2.0, 2.0, 80, ZoneStatus.ACTIVE, 0, None, None, date(2024, 1, 3), None)


def test_failure_rate_is_a_proportion_of_tested_zones():
    # Gaps straight through the zone: never overlaps, closes below it.
    gapped = event_study_metrics([_demand()], _panel([12, 8, 7]))["overall"]
    assert gapped["tested"] == 1 and gapped["broken"] == 1 and gapped["failure_rate"] == 1.0
    # Never returns to the zone: neither tested nor broken.
    untouched = event_study_metrics([_demand()], _panel([12, 13, 14]))["overall"]
    assert untouched["tested"] == 0 and untouched["broken"] == 0 and untouched["failure_rate"] == 0.0


def test_a_zone_that_holds_is_tested_but_not_broken():
    held = event_study_metrics([_demand()], _panel([12, 9.9, 11, 12]))["overall"]
    assert held["tested"] == 1 and held["broken"] == 0
