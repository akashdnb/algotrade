from datetime import date
from decimal import Decimal

from algotrade.domain.zones import Zone, ZoneProximity, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.config import DemandSupplyConfig
from algotrade.zones.proximity import classify_zone_proximity


def _zone(status=ZoneStatus.ACTIVE):
    return Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1"), 2.0, 2.0, 0, status, 0, None, None, date(2024,1,3), None)


def test_proximity_boundaries():
    config = DemandSupplyConfig(ZoneTimeframe.DAILY)
    assert classify_zone_proximity(_zone(), Decimal("9.5"), config) == ZoneProximity.IN_ZONE
    assert classify_zone_proximity(_zone(), Decimal("10.1"), config) == ZoneProximity.NEAR
    assert classify_zone_proximity(_zone(), Decimal("12"), config) == ZoneProximity.FAR
    assert classify_zone_proximity(_zone(ZoneStatus.BROKEN), Decimal("9.5"), config) == ZoneProximity.BROKEN
