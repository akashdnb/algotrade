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
