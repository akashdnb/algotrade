"""Tests for rupee arithmetic.

Money is the one thing in this system that must never be a float. These tests
pin the rounding mode and the paise quantum, because both are silent-error
sources: Python's Decimal defaults to banker's rounding, which is NOT the
financial convention.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.domain import money


def test_rupees_accepts_str_int_and_decimal():
    assert money.rupees("100.50") == Decimal("100.50")
    assert money.rupees(100) == Decimal("100.00")
    assert money.rupees(Decimal("100.5")) == Decimal("100.50")


def test_rupees_quantizes_to_paise():
    assert money.rupees("100.567") == Decimal("100.57")
    assert money.rupees("100.564") == Decimal("100.56")


def test_rounding_is_half_up_not_bankers():
    """Python's Decimal default is ROUND_HALF_EVEN, which would make
    2.005 -> 2.00. Financial convention is half-up. Getting this wrong biases
    every rounded amount toward even paise, systematically."""
    assert money.rupees("2.005") == Decimal("2.01")
    assert money.rupees("2.015") == Decimal("2.02")
    assert money.rupees("2.025") == Decimal("2.03")   # half-even would give 2.02


def test_negative_amounts_round_away_from_zero():
    assert money.rupees("-2.005") == Decimal("-2.01")


def test_a_float_is_refused_outright():
    """Accepting a float would silently import the error it carries. The caller
    must decide how to stringify it, at the point where the precision is known."""
    with pytest.raises(TypeError, match="float"):
        money.rupees(100.5)


def test_zero_and_none_helpers():
    assert money.ZERO == Decimal("0.00")
    assert money.rupees(None) == Decimal("0.00")


def test_sum_rupees_is_exact_over_many_terms():
    """0.1 + 0.2 == 0.3 exactly, which is the entire reason for Decimal."""
    total = money.sum_rupees([money.rupees("0.10"), money.rupees("0.20")])
    assert total == Decimal("0.30")

    many = money.sum_rupees([money.rupees("0.01")] * 1000)
    assert many == Decimal("10.00")


def test_sum_of_nothing_is_zero_not_an_error():
    assert money.sum_rupees([]) == money.ZERO


def test_weight_of_returns_a_float_ratio_and_survives_zero_denominator():
    assert money.weight_of(money.rupees("25.00"), money.rupees("100.00")) == 0.25
    # A zero-NAV portfolio has no meaningful weights; 0.0 beats ZeroDivisionError
    # in a valuation loop that must still produce a snapshot.
    assert money.weight_of(money.rupees("25.00"), money.ZERO) == 0.0


def test_percent_change_is_signed_and_guards_zero_base():
    assert money.percent_change(money.rupees("110"), money.rupees("100")) == pytest.approx(0.10)
    assert money.percent_change(money.rupees("90"), money.rupees("100")) == pytest.approx(-0.10)
    assert money.percent_change(money.rupees("90"), money.ZERO) == 0.0
