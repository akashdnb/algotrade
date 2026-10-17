from datetime import date
from decimal import Decimal

import polars as pl

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.config import DemandSupplyConfig
from algotrade.zones.lifecycle import update_zone_status


def test_touch_then_break_is_terminal():
    zone = Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1"), 2.0, 2.0, 0, ZoneStatus.ACTIVE, 0, None, None, date(2024,1,3), None)
    candles = pl.DataFrame([
        {"date": date(2024,1,4), "low": 9.2, "high": 10.2, "close": 9.5},
        {"date": date(2024,1,5), "low": 8.5, "high": 9.0, "close": 8.7},
    ])
    result = update_zone_status(zone, candles, DemandSupplyConfig(ZoneTimeframe.DAILY))
    assert result.status == ZoneStatus.BROKEN
    assert result.touch_count == 1


def test_touch_count_increments_only_on_reentry():
    zone = Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1"), 2.0, 2.0, 0, ZoneStatus.ACTIVE, 0, None, None, date(2024,1,3), None)
    candles = pl.DataFrame([
        {"date": date(2024,1,4), "low": 9.2, "high": 10.2, "close": 9.8},
        {"date": date(2024,1,5), "low": 9.3, "high": 10.1, "close": 9.9},
        {"date": date(2024,1,6), "low": 10.5, "high": 11.0, "close": 10.8},
        {"date": date(2024,1,7), "low": 9.4, "high": 10.0, "close": 9.7},
    ])
    result = update_zone_status(zone, candles, DemandSupplyConfig(ZoneTimeframe.DAILY))
    assert result.status == ZoneStatus.TESTED
    assert result.touch_count == 2
    assert result.first_tested_at == date(2024,1,4)
    assert result.last_tested_at == date(2024,1,7)
