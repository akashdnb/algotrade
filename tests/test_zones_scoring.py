from datetime import date
from decimal import Decimal

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.zones.config import DemandSupplyConfig
from algotrade.zones.scoring import score_zone


def test_score_is_bounded_and_freshness_changes_with_age():
    config = DemandSupplyConfig(ZoneTimeframe.DAILY)
    zone = Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), Decimal("1"), 2.0, 2.0, 0, ZoneStatus.ACTIVE, 0, None, None, date(2024, 1, 3), None)
    fresh = score_zone(zone, config=config, as_of_date=date(2024, 1, 3))
    old = score_zone(zone, config=config, as_of_date=date(2025, 1, 3))
    assert 0 <= old <= fresh <= 100
