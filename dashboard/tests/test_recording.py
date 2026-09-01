"""The write path. Every test here guards a way the book could go wrong."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from algotrade.domain.models import Action, Item, ItemStatus, RecommendationSet
from app.recording import (
    RecordingError,
    build_fills,
    open_items,
    outstanding_quantity,
    parse_money,
    parse_quantity,
)


def _set() -> RecommendationSet:
    return RecommendationSet(
        set_id="paper_momentum_v1_001:2026-09:r1",
        portfolio_id="paper_momentum_v1_001",
        rebalance_run_id="paper_momentum_v1_001:2026-09",
        period="2026-09",
        as_of_session=date(2026, 9, 1),
        revision=1,
        items=[
            Item(seq=1, action=Action.SELL, symbol="OLDCO", quantity=10,
                 reference_price=Decimal("100.00")),
            Item(seq=2, action=Action.BUY, symbol="NEWCO", quantity=5,
                 reference_price=Decimal("200.00")),
            Item(seq=3, action=Action.BUY, symbol="PARTIAL", quantity=8,
                 reference_price=Decimal("50.00"),
                 status=ItemStatus.PARTIAL, executed_quantity=3),
        ],
    )


# ------------------------------------------------------------- parsing ---


@pytest.mark.parametrize("raw,expected", [
    ("100", Decimal("100")), ("4,075.50", Decimal("4075.50")),
    (" 12.34 ", Decimal("12.34")),
])
def test_prices_parse_from_what_a_human_types(raw, expected):
    assert parse_money(raw, field_name="price") == expected


@pytest.mark.parametrize("raw", ["", "abc", "12 34", "1.2.3", "-5"])
def test_a_price_that_is_not_a_number_is_refused(raw):
    """Coercing `12 34` to something would record a wrong number with total
    confidence. Refusing is the only safe reading."""
    with pytest.raises(RecordingError):
        parse_money(raw, field_name="price")


def test_quantities_must_be_whole_shares():
    assert parse_quantity("7", field_name="qty") == 7
    with pytest.raises(RecordingError):
        parse_quantity("7.5", field_name="qty")


# --------------------------------------------------------------- fills ---


def test_a_blank_quantity_means_not_traded(): 
    """Not "traded zero". A rebalance is routinely part-executed and every
    instruction appears on the form whether or not it was filled."""
    fills = build_fills(_set(), {"qty_1": "", "qty_2": "5", "price_2": "201"},
                        batch_id="b1")
    assert [f.seq for f in fills] == [2]


def test_a_form_with_nothing_filled_is_refused():
    with pytest.raises(RecordingError, match="Nothing to record"):
        build_fills(_set(), {"qty_1": "", "qty_2": "0"}, batch_id="b1")


def test_over_filling_an_instruction_is_refused_before_anything_is_written():
    with pytest.raises(RecordingError, match="exceeds"):
        build_fills(_set(), {"qty_2": "9", "price_2": "200"}, batch_id="b1")


def test_a_partial_fill_is_measured_against_what_remains():
    """seq 3 is 8 shares with 3 already done: 5 may still be recorded, 6 not."""
    fills = build_fills(_set(), {"qty_3": "5", "price_3": "50"}, batch_id="b1")
    assert fills[0].quantity == 5
    with pytest.raises(RecordingError, match="exceeds"):
        build_fills(_set(), {"qty_3": "6", "price_3": "50"}, batch_id="b1")


def test_a_fill_for_an_unknown_instruction_is_refused():
    """A tampered or stale form must not create a trade with no instruction."""
    with pytest.raises(RecordingError, match="not part of"):
        build_fills(_set(), {"qty_99": "1", "price_99": "10"}, batch_id="b1")


def test_a_missing_price_is_refused():
    """Recording a quantity with no price would book a trade at nothing."""
    with pytest.raises(RecordingError, match="price is required"):
        build_fills(_set(), {"qty_2": "5", "price_2": ""}, batch_id="b1")


def test_charges_default_to_zero_rather_than_failing():
    fills = build_fills(_set(), {"qty_2": "5", "price_2": "200"}, batch_id="b1")
    assert fills[0].charges == Decimal("0")


def test_the_batch_id_travels_on_every_fill():
    """It is what makes a double-tapped Submit recognisable; paper fills have
    no broker order id to make them idempotent individually."""
    fills = build_fills(_set(), {"qty_2": "5", "price_2": "200"}, batch_id="b-xyz")
    assert "batch:b-xyz" in fills[0].note


# --------------------------------------------------------------- order ---


def test_sells_are_listed_before_buys():
    """T+1 settlement funds the buys, so the list must be workable top to
    bottom."""
    assert [i.symbol for i in open_items(_set())][0] == "OLDCO"


def test_outstanding_never_goes_negative():
    item = Item(seq=1, action=Action.BUY, symbol="X", quantity=5,
                reference_price=Decimal("1"), executed_quantity=9)
    assert outstanding_quantity(item) == 0
