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
