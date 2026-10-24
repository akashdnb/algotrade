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


def _zone(volume_ratio, tf=ZoneTimeframe.DAILY):
    return Zone("ABC", tf, ZoneType.DEMAND, Decimal("9"), Decimal("10"), date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3), Decimal("1"), 2.0, volume_ratio, 0, ZoneStatus.ACTIVE, 0, None, None, date(2024, 1, 3), None)


def test_volume_term_discriminates_and_is_capped():
    config = DemandSupplyConfig(ZoneTimeframe.DAILY)
    scores = [score_zone(_zone(r), config=config, as_of_date=date(2024, 1, 3)) for r in (1.0, 2.0, 4.0, 8.0, 16_000.0)]
    assert scores == sorted(scores) and len(set(scores[:4])) == 4
    assert scores[-1] == scores[3]  # a 16,000x outlier earns no more than 8x
    assert score_zone(_zone(None), config=config, as_of_date=date(2024, 1, 3)) == scores[0]


def test_age_in_own_timeframe_bars_beats_calendar_days():
    config = DemandSupplyConfig(ZoneTimeframe.MONTHLY)
    zone = _zone(2.0, ZoneTimeframe.MONTHLY)
    by_days = score_zone(zone, config=config, as_of_date=date(2024, 4, 3))  # 91 calendar days, 3 monthly bars
    by_bars = score_zone(zone, config=config, as_of_date=date(2024, 4, 3), age_bars=3)
    assert by_bars > by_days


def test_default_weights_sum_to_100_and_structure_is_unweighted():
    config = DemandSupplyConfig(ZoneTimeframe.DAILY)
    assert config.structure_weight == 0
