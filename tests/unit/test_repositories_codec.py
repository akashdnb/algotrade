"""Tests for the Decimal <-> Decimal128 boundary.

This is the only place money changes representation, so it is the only place
precision can silently vanish. Every conversion is tested in both directions.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128

from algotrade.domain import money
from algotrade.repositories import codec


def test_money_round_trips_exactly():
    original = money.rupees("284310.22")
    stored = codec.to_mongo_money(original)
    assert isinstance(stored, Decimal128)
    assert codec.from_mongo_money(stored) == original


def test_a_large_amount_keeps_every_paisa():
    """Decimal128 has 34 significant digits; a float would lose paise here."""
    original = money.rupees("99999999.99")
    assert codec.from_mongo_money(codec.to_mongo_money(original)) == original


def test_negative_amounts_survive():
    original = money.rupees("-1234.56")
    assert codec.from_mongo_money(codec.to_mongo_money(original)) == original


def test_none_maps_to_none_in_both_directions():
    assert codec.to_mongo_money(None) is None
    assert codec.from_mongo_money(None) is None


def test_reading_a_float_from_mongo_is_refused():
    """A float in a money field means someone wrote one, bypassing this codec.
    Reading it silently would launder that error into the domain."""
    with pytest.raises(TypeError, match="float"):
        codec.from_mongo_money(100.5)


def test_reading_a_plain_int_is_accepted_as_a_whole_rupee_amount():
    assert codec.from_mongo_money(100) == money.rupees("100.00")


def test_business_dates_are_stored_as_strings_not_datetimes():
    """A trading date is a calendar fact, not an instant. Stored as a Date,
    a timezone conversion can move it a day -- and IST is UTC+5:30, so the
    boundary is crossed by any UTC-midnight normalisation."""
    assert codec.to_mongo_date(date(2026, 9, 30)) == "2026-09-30"
    assert codec.from_mongo_date("2026-09-30") == date(2026, 9, 30)


def test_a_none_date_round_trips():
    assert codec.to_mongo_date(None) is None
    assert codec.from_mongo_date(None) is None


def test_instants_stay_datetimes_and_are_forced_to_utc():
    aware = datetime(2026, 9, 30, 14, 35, tzinfo=timezone.utc)
    assert codec.to_mongo_instant(aware) == aware


def test_a_naive_instant_is_rejected_rather_than_assumed_to_be_utc():
    """Assuming naive means UTC is how a 5.5-hour error enters an audit log."""
    with pytest.raises(ValueError, match="timezone"):
        codec.to_mongo_instant(datetime(2026, 9, 30, 14, 35))


def test_from_mongo_instant_restores_utc_awareness():
    """pymongo returns naive datetimes in UTC by default; the codec re-attaches
    the tzinfo so the domain never handles a naive instant."""
    naive_from_driver = datetime(2026, 9, 30, 14, 35)
    restored = codec.from_mongo_instant(naive_from_driver)
    assert restored.tzinfo is timezone.utc
    assert restored.hour == 14


def test_encode_doc_converts_every_money_field_it_is_told_about():
    doc = codec.encode_doc(
        {"nav": money.rupees("100.00"), "name": "x", "qty": 5},
        money_fields=("nav",),
    )
    assert isinstance(doc["nav"], Decimal128)
    assert doc["name"] == "x"
    assert doc["qty"] == 5


def test_encode_doc_leaves_a_missing_field_absent_rather_than_writing_null():
    doc = codec.encode_doc({"name": "x"}, money_fields=("nav",))
    assert "nav" not in doc


def test_decode_doc_is_the_inverse_of_encode_doc():
    original = {"nav": money.rupees("100.00"), "cash": money.rupees("5.25"), "n": 3}
    encoded = codec.encode_doc(original, money_fields=("nav", "cash"))
    decoded = codec.decode_doc(encoded, money_fields=("nav", "cash"))
    assert decoded == original


# ------------------------------------------------- non-money precision ------


def test_units_keep_three_decimals_through_the_codec():
    """The bug this exists to prevent: units quantized to paise. A 20k
    contribution at unit NAV ~120 buys 166.667 units; storing 166.67 leaks
    value on every contribution and corrupts unit NAV, which is the whole
    mechanism that keeps a deposit from looking like a return."""
    units = Decimal("2166.667")
    assert codec.from_mongo_decimal(
        codec.to_mongo_decimal(units, places=3), places=3) == units
    # what the money path would have done
    assert codec.from_mongo_money(codec.to_mongo_money(units)) == Decimal("2166.67")


def test_unit_nav_keeps_four_decimals():
    nav = Decimal("117.8305")
    assert codec.from_mongo_decimal(
        codec.to_mongo_decimal(nav, places=4), places=4) == nav


def test_units_are_not_declared_as_money_anywhere():
    """A regression guard: putting `units` back into MONEY_FIELDS silently
    reintroduces paise quantization."""
    from algotrade.repositories import schema

    for collection, fields in schema.MONEY_FIELDS.items():
        assert "units" not in fields, f"{collection}: units must not be a money field"
        assert "unit_nav" not in fields


def test_precise_fields_round_trip_via_encode_decode():
    spec = {"units": 3, "unit_nav": 4}
    original = {"units": Decimal("2166.667"), "unit_nav": Decimal("117.8305"),
                "name": "x"}
    encoded = codec.encode_precise(original, spec)
    assert isinstance(encoded["units"], Decimal128)
    assert codec.decode_precise(encoded, spec) == original


def test_a_float_is_refused_for_precise_fields_too():
    with pytest.raises(TypeError, match="float"):
        codec.to_mongo_decimal(2166.667, places=3)
