from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain.zones import Timeframe, Zone, ZoneStatus, ZoneType
from algotrade.zones.config import DemandSupplyConfig
from algotrade.zones.hygiene import dedupe_zones, drop_expired


def _zone(low, high, *, score=50, status=ZoneStatus.ACTIVE, created=date(2024, 1, 10), kind=ZoneType.DEMAND, tf=Timeframe.DAILY, symbol="ABC"):
    return Zone(symbol, tf, kind, Decimal(low), Decimal(high), created, created, created, Decimal("1"), 2.0, 2.0, score, status, 0, None, None, created, None)


def test_overlapping_live_zones_keep_the_strongest():
    weak, strong = _zone("100", "110", score=40), _zone("102", "112", score=70, created=date(2024, 2, 1))
    assert dedupe_zones([weak, strong], 0.5) == [strong]


def test_tie_goes_to_the_more_recent_zone():
    old, new = _zone("100", "110", created=date(2024, 1, 1)), _zone("100", "110", created=date(2024, 3, 1))
    assert dedupe_zones([old, new], 0.5) == [new]


def test_distinct_levels_direction_and_timeframe_are_not_merged():
    zones = [_zone("100", "110"), _zone("200", "210"), _zone("100", "110", kind=ZoneType.SUPPLY), _zone("100", "110", tf=Timeframe.WEEKLY)]
    assert dedupe_zones(zones, 0.5) == zones


def test_broken_zones_pass_through_and_order_is_preserved():
    broken = _zone("100", "110", status=ZoneStatus.BROKEN)
    live = _zone("100", "110", score=60)
    assert dedupe_zones([broken, live], 0.5) == [broken, live]


def test_disabled_dedupe_and_expiry_are_identity():
    zones = [_zone("100", "110"), _zone("100", "110", created=date(2024, 2, 1))]
    assert dedupe_zones(zones, None) == zones
    assert drop_expired(zones, {z.zone_id: 9999 for z in zones}, None) == zones


def test_expiry_drops_zones_older_than_the_limit():
    old, fresh = _zone("100", "110", created=date(2020, 1, 1)), _zone("200", "210")
    out = drop_expired([old, fresh], {old.zone_id: 251, fresh.zone_id: 250}, 250)
    assert out == [fresh]


def test_config_validates_the_new_fields():
    with pytest.raises(ValueError):
        DemandSupplyConfig(Timeframe.DAILY, max_age_bars=0)
    with pytest.raises(ValueError):
        DemandSupplyConfig(Timeframe.DAILY, dedupe_overlap=0.0)
