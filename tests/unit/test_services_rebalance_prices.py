"""Tests for assembling the prices a rebalance decision needs (design §5).

The bug this exists to prevent, found 2026-09-04: the signal set stores only
the top 60 ranks, and held names outside it were defaulted to **zero**. Every
sell instruction then read `@ Rs 0.00` and contributed nothing to investable
capital, for seven names that had perfectly good prices in the panel and were
being sold only because they had fallen out of the ranking.

It had never bitten because every execution so far was a buy. It became
dangerous the day the shadow portfolio started auto-filling at the reference
price, which would have booked seven sales at zero and realised a total loss
on each.
"""

from __future__ import annotations

from decimal import Decimal

from algotrade.services.valuation import merge_prices

D = Decimal


def test_ranked_names_keep_the_close_the_ranking_used():
    """Not re-read from the panel: sizing must use the same number that
    justified the name, or the two can disagree across a panel rebuild."""
    prices, unpriced = merge_prices(
        {"A": D("10.00")}, ["A"], {"A": D("99.00")})
    assert prices["A"] == D("10.00")
    assert unpriced == []


def test_a_held_name_outside_the_ranking_is_priced_from_the_panel():
    """The case that was zero. Falling out of the top 60 is why the name is
    being sold; it says nothing about what the name is worth."""
    prices, unpriced = merge_prices(
        {"A": D("10.00")}, ["A", "HFCL"], {"HFCL": D("231.44")})
    assert prices["HFCL"] == D("231.44")
    assert unpriced == []


def test_a_held_name_that_cannot_be_priced_is_reported_never_zeroed():
    """A sell instruction at zero is not a conservative estimate, it is a
    wrong one. The caller refuses rather than emitting it."""
    prices, unpriced = merge_prices({"A": D("10.00")}, ["A", "GONE"], {})
    assert "GONE" not in prices
    assert unpriced == ["GONE"]


def test_a_zero_in_the_panel_counts_as_unpriced():
    """Zero is the value this bug produced. Accepting it from the panel would
    reintroduce the same instruction through a different door."""
    prices, unpriced = merge_prices(
        {}, ["SUSPENDED"], {"SUSPENDED": D("0.00")})
    assert "SUSPENDED" not in prices
    assert unpriced == ["SUSPENDED"]


def test_a_negative_price_counts_as_unpriced():
    prices, unpriced = merge_prices({}, ["ODD"], {"ODD": D("-1.00")})
    assert unpriced == ["ODD"]


def test_names_that_are_ranked_but_not_held_are_kept():
    """The buy candidates. Every target name needs a price to be sized."""
    prices, _ = merge_prices(
        {"A": D("10.00"), "B": D("20.00")}, ["A"], {})
    assert set(prices) == {"A", "B"}


def test_unpriced_is_sorted_and_deduplicated():
    prices, unpriced = merge_prices({}, ["B", "A", "B"], {})
    assert unpriced == ["A", "B"]


def test_nothing_held_and_nothing_ranked_is_not_an_error():
    assert merge_prices({}, [], {}) == ({}, [])


def test_the_ranked_mapping_is_not_mutated():
    """It belongs to the SignalSet; a rebalance must not edit it in place."""
    ranked = {"A": D("10.00")}
    merge_prices(ranked, ["HFCL"], {"HFCL": D("231.44")})
    assert ranked == {"A": D("10.00")}
