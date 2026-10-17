from datetime import date
from decimal import Decimal

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.confluence import calculate_confluence


def _zone(tf, low, high):
    return Zone("ABC", tf, ZoneType.DEMAND, Decimal(str(low)), Decimal(str(high)), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1"), 2.0, 2.0, 0, ZoneStatus.ACTIVE, 0, None, None, date(2024,1,3), None)


def test_overlapping_daily_weekly_zones_form_confluence():
    result = calculate_confluence([_zone(ZoneTimeframe.DAILY, 9, 10), _zone(ZoneTimeframe.WEEKLY, 9.5, 10.5)], Decimal("10"))
    assert len(result) == 1
    assert result[0].timeframes == (ZoneTimeframe.DAILY, ZoneTimeframe.WEEKLY)
    assert result[0].overlap_low == Decimal("9.5")
