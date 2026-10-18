import time
from datetime import date, timedelta
from decimal import Decimal

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.confluence import calculate_confluence


def _zone(tf, low, high, *, i=0):
    day = date(2024, 1, 1) + timedelta(days=i)
    return Zone("ABC", tf, ZoneType.DEMAND, Decimal(str(low)), Decimal(str(high)), day, day + timedelta(days=1), day + timedelta(days=2), Decimal("1"), 2.0, 2.0, 0, ZoneStatus.ACTIVE, 0, None, None, day + timedelta(days=2), None)


def test_overlapping_daily_weekly_zones_form_confluence():
    result = calculate_confluence([_zone(ZoneTimeframe.DAILY, 9, 10), _zone(ZoneTimeframe.WEEKLY, 9.5, 10.5)], Decimal("10"))
    assert len(result) == 1
    assert result[0].timeframes == (ZoneTimeframe.DAILY, ZoneTimeframe.WEEKLY)
    assert result[0].overlap_low == Decimal("9.5")


def test_many_zones_per_timeframe_stays_fast_and_finds_overlap():
    # A symbol that has accumulated many still-active zones on one timeframe
    # (realistic after years of history) must not make confluence combinatorial
    # in the total zone count -- only in the number of distinct timeframes (3).
    non_overlapping = [_zone(ZoneTimeframe.DAILY, 100 + i, 101 + i, i=i) for i in range(30)]
    overlapping_daily = _zone(ZoneTimeframe.DAILY, 8, 10, i=100)
    overlapping_weekly = _zone(ZoneTimeframe.WEEKLY, 9, 11, i=101)
    zones = non_overlapping + [overlapping_daily, overlapping_weekly]

    start = time.time()
    result = calculate_confluence(zones, Decimal("10"))
    assert time.time() - start < 2.0

    assert len(result) == 1
    assert result[0].timeframes == (ZoneTimeframe.DAILY, ZoneTimeframe.WEEKLY)
    assert result[0].overlap_low == Decimal("9")
    assert result[0].overlap_high == Decimal("10")
