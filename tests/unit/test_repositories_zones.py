from datetime import date
from decimal import Decimal

from algotrade.domain.zones import Zone, ZoneStatus, ZoneTimeframe, ZoneType
from algotrade.repositories import schema
from algotrade.repositories.zones import ZoneRepository


def test_zone_schema_is_registered():
    assert "zones" in schema.BY_NAME
    assert schema.BY_NAME["zones"].indexes


def test_zone_document_has_deterministic_key_and_decimal_fields():
    zone = Zone("ABC", ZoneTimeframe.DAILY, ZoneType.DEMAND, Decimal("9.00"), Decimal("10.00"), date(2024,1,1), date(2024,1,2), date(2024,1,3), Decimal("1.20"), 2.0, 1.5, 75, ZoneStatus.ACTIVE, 0, None, None, date(2024,1,3), None)
    doc = ZoneRepository._document(zone)
    assert ZoneRepository.key(zone) == "ABC:daily:demand:2024-01-01:2024-01-02"
    assert str(doc["zone_low"].to_decimal()) == "9.00"
    assert doc["status"] == "active"
