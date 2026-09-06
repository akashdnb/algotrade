"""Tests for correcting an estimated charge (design §9).

The rule the whole module exists to hold: **a correction is a new ledger
entry, never an edit to the execution.** Executions are the trade and tax
record and are never patched in place -- editing one would also destroy the
ability to say what was believed when the trade was made.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.services import charges

ORDER = "251001000123456"


def _execution(_id="e1", symbol="RELIANCE", charge="15.34", order_id=ORDER):
    return {"_id": _id, "symbol": symbol, "charges": Decimal(charge),
            "broker_order_id": order_id}


# ---------------------------------------------------------- corrections ---

def test_a_charge_that_matches_produces_no_material_correction():
    got = charges.reconcile([_execution()], {ORDER: Decimal("15.34")})
    assert got.ok
    assert got.material == []


def test_an_understated_estimate_is_negative_from_the_books_side():
    """The direction end-of-day STT and stamp duty push it: the estimate was
    too low and the book owes more."""
    got = charges.reconcile([_execution(charge="15.34")],
                            {ORDER: Decimal("22.00")})
    assert got.corrections[0].delta == Decimal("-6.66")
    assert got.net == Decimal("-6.66")


def test_an_overstated_estimate_is_positive():
    got = charges.reconcile([_execution(charge="22.00")],
                            {ORDER: Decimal("15.34")})
    assert got.corrections[0].delta == Decimal("6.66")


def test_a_rounding_difference_is_not_material():
    """A paisa between the rate card and the contract note is not a finding,
    and reporting it would bury the ones that are."""
    got = charges.reconcile([_execution(charge="15.34")],
                            {ORDER: Decimal("15.35")})
    assert got.corrections[0].material is False
    assert got.ok


def test_the_materiality_threshold_is_inclusive():
    got = charges.reconcile([_execution(charge="15.00")],
                            {ORDER: Decimal("15.50")})
    assert got.corrections[0].material


# ------------------------------------------------------------- matching ---

def test_matching_is_on_the_broker_order_id_not_the_symbol():
    """The same name can be traded twice in a session, and the two-day
    protocol makes that ordinary rather than exotic."""
    got = charges.reconcile(
        [_execution("e1", order_id="A"), _execution("e2", order_id="B")],
        {"A": Decimal("10.00"), "B": Decimal("20.00")})
    assert {c.execution_id: c.actual for c in got.corrections} == {
        "e1": Decimal("10.00"), "e2": Decimal("20.00")}


def test_an_execution_absent_from_the_note_is_reported_not_assumed_correct():
    """One nobody verified. Silently passing it would let the reconciliation
    claim a completeness it does not have."""
    got = charges.reconcile([_execution("e1")], {})
    assert got.unmatched == ["e1"]
    assert not got.ok


def test_an_execution_with_no_broker_order_id_cannot_be_matched():
    got = charges.reconcile([_execution(order_id="")], {ORDER: Decimal("1")})
    assert got.unmatched == ["e1"]


def test_an_execution_with_no_recorded_charge_reads_as_zero():
    """Settled without a rate card, which records zero. The correction then
    carries the whole amount rather than crashing."""
    got = charges.reconcile([{"_id": "e1", "symbol": "X",
                              "broker_order_id": ORDER}],
                            {ORDER: Decimal("15.34")})
    assert got.corrections[0].delta == Decimal("-15.34")


# ------------------------------------------------------------ adjustment ---

def test_the_correction_is_an_adjustment_not_a_charges_entry():
    """`charges` records what a trade cost when it happened. Reusing it would
    make the correction indistinguishable from the original, so the same trade
    would appear to have been charged twice."""
    correction = charges.reconcile([_execution()],
                                   {ORDER: Decimal("22.00")}).corrections[0]
    entry = charges.adjustment(correction)
    assert entry["kind"] == "adjustment"
    assert entry["amount"] == Decimal("-6.66")


def test_the_adjustment_names_the_execution_it_belongs_to():
    """So the ledger can be read back to the trade it corrects."""
    correction = charges.reconcile([_execution()],
                                   {ORDER: Decimal("22.00")}).corrections[0]
    entry = charges.adjustment(correction)
    assert entry["execution_id"] == "e1"
    assert entry["symbol"] == "RELIANCE"


def test_the_note_explains_the_direction_in_words():
    correction = charges.reconcile([_execution()],
                                   {ORDER: Decimal("22.00")}).corrections[0]
    assert "understated" in charges.adjustment(correction)["note"]


def test_an_overstatement_says_so():
    correction = charges.reconcile([_execution(charge="22.00")],
                                   {ORDER: Decimal("15.34")}).corrections[0]
    assert "overstated" in charges.adjustment(correction)["note"]


# ----------------------------------------------------------------- net ---

def test_the_net_sums_only_material_corrections():
    got = charges.reconcile(
        [_execution("e1", order_id="A", charge="15.00"),
         _execution("e2", order_id="B", charge="10.00")],
        {"A": Decimal("15.01"), "B": Decimal("20.00")})
    assert got.net == Decimal("-10.00")
