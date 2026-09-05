"""Tests for assembling symbol identity (design §6).

Three properties matter here, and each is a decision rather than a mechanism:

* the map holds only instruments this system can actually trade;
* the broker outranks the corporate-actions feed on ISIN;
* a symbol with no tradingsymbol is skipped, never half-written.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from algotrade.brokers.base import BrokerHolding
from algotrade.services import symbols

HFCL = "INE548A01028"
RELIANCE = "INE002A01018"


def _instrument(**kwargs) -> dict:
    row = {"exchange": "NSE", "instrument_type": "EQ", "segment": "NSE",
           "tradingsymbol": "HFCL", "instrument_token": 548865}
    row.update(kwargs)
    return row


def _held(symbol="HFCL", isin=HFCL) -> BrokerHolding:
    return BrokerHolding(symbol=symbol, isin=isin, quantity=30,
                         average_price=Decimal("224.17"))


# ----------------------------------------------------------- feed ticker ---

def test_the_feed_ticker_appends_the_nse_suffix():
    assert symbols.feed_ticker("HFCL") == "HFCL.NS"


def test_an_empty_symbol_has_no_feed_ticker():
    """`.NS` alone would be a lookup for nothing that silently returns a
    price for something."""
    assert symbols.feed_ticker("") == ""


# ---------------------------------------------------------- instruments ---

def test_an_nse_equity_becomes_a_mapping():
    got = symbols.from_instruments([_instrument()])
    assert set(got) == {"HFCL"}
    assert got["HFCL"].kite_instrument_token == 548865
    assert got["HFCL"].feed_ticker == "HFCL.NS"
    assert got["HFCL"].series == "EQ"


def test_a_be_series_tradingsymbol_is_split_into_symbol_and_series():
    """Kite encodes the series as a suffix rather than a field, so the book's
    plain `HFCL` would never match `HFCL-BE` without this."""
    got = symbols.from_instruments([_instrument(tradingsymbol="HFCL-BE")])
    assert set(got) == {"HFCL"}
    assert got["HFCL"].series == "BE"
    assert got["HFCL"].kite_tradingsymbol == "HFCL-BE"


def test_a_hyphenated_name_that_is_not_a_series_is_left_alone():
    """`M-M` is Mahindra & Mahindra. Treating `M` as a series would rename the
    company -- the exact failure this map exists to prevent."""
    got = symbols.from_instruments([_instrument(tradingsymbol="M-M")])
    assert set(got) == {"M-M"}
    assert got["M-M"].series == "EQ"


def test_a_multi_letter_suffix_is_part_of_the_symbol():
    """`MRO-TEK` is a company; `TEK` is not a series code."""
    got = symbols.from_instruments([_instrument(tradingsymbol="MRO-TEK")])
    assert set(got) == {"MRO-TEK"}


def test_other_exchanges_are_excluded():
    assert symbols.from_instruments([_instrument(exchange="BSE")]) == {}


def test_derivatives_and_other_types_are_excluded():
    """The dump carries futures, options, currency and commodities, none of
    which this system can trade."""
    assert symbols.from_instruments([_instrument(instrument_type="FUT")]) == {}


def test_indices_are_excluded():
    """NIFTY 50 shares the exchange AND the instrument_type with every stock;
    only the segment separates them. Without that filter it enters the map as
    something you could buy, and nothing downstream questions a symbol that
    resolves."""
    got = symbols.from_instruments([
        _instrument(tradingsymbol="NIFTY 50", segment="INDICES")])
    assert got == {}


@pytest.mark.parametrize("suffix", ["SG", "GS", "TB", "GB", "N0", "N9",
                                    "NA", "SM", "BZ", "IV", "SF", "ST"])
def test_non_equity_series_are_excluded(suffix):
    """Government paper, debentures, SME and trade-to-trade all arrive as
    instrument_type EQ. On 2026-09-05 they were 6,883 of 10,126 cash-segment
    rows -- three times the size of the universe the map describes."""
    got = symbols.from_instruments([
        _instrument(tradingsymbol=f"760SG2033-{suffix}")])
    assert got == {}


def test_the_be_series_is_kept_because_it_is_the_same_equity():
    got = symbols.from_instruments([_instrument(tradingsymbol="HFCL-BE")])
    assert set(got) == {"HFCL"}


def test_eq_wins_when_a_scrip_appears_as_both_eq_and_be():
    """Otherwise which row survives depends on file order, and the panel and
    the book both use the plain series."""
    for order in ([_instrument(tradingsymbol="HFCL"),
                   _instrument(tradingsymbol="HFCL-BE", instrument_token=1)],
                  [_instrument(tradingsymbol="HFCL-BE", instrument_token=1),
                   _instrument(tradingsymbol="HFCL")]):
        got = symbols.from_instruments(order)
        assert got["HFCL"].series == "EQ"
        assert got["HFCL"].kite_tradingsymbol == "HFCL"


# --------------------------------------------------------- split_series ---

@pytest.mark.parametrize("given,symbol,series", [
    ("HFCL", "HFCL", "EQ"),
    ("HFCL-BE", "HFCL", "BE"),
    ("M-M", "M-M", "EQ"),
    ("MRO-TEK", "MRO-TEK", "EQ"),
    ("760SG2033-SG", "760SG2033", "SG"),
])
def test_split_series_reads_the_suffix_only_when_it_is_a_series_code(
        given, symbol, series):
    assert symbols.split_series(given) == (symbol, series)


def test_a_row_without_a_tradingsymbol_is_skipped():
    assert symbols.from_instruments([_instrument(tradingsymbol="")]) == {}


def test_a_missing_token_is_none_rather_than_zero():
    """Zero is a valid-looking token that addresses nothing."""
    got = symbols.from_instruments([_instrument(instrument_token=None)])
    assert got["HFCL"].kite_instrument_token is None


# --------------------------------------------------------------- enrich ---

def test_an_isin_from_corporate_actions_is_applied():
    base = symbols.from_instruments([_instrument()])
    got = symbols.enrich(base, isins={"HFCL": HFCL})
    assert got[0].isin == HFCL


def test_the_broker_outranks_the_corporate_actions_feed():
    """The broker is the party we reconcile against. If the feed disagrees,
    the feed is what is stale."""
    base = symbols.from_instruments([_instrument()])
    got = symbols.enrich(base, isins={"HFCL": RELIANCE}, demat=[_held()])
    assert got[0].isin == HFCL


def test_a_symbol_the_instruments_dump_does_not_know_is_skipped():
    """Without a tradingsymbol and token the row addresses nothing at Kite,
    and a half-row in an identity table looks like an answer."""
    got = symbols.enrich({}, isins={"GHOST": HFCL}, demat=[_held("GHOST")])
    assert got == []


def test_enrichment_leaves_everything_else_untouched():
    base = symbols.from_instruments([_instrument(tradingsymbol="HFCL-BE")])
    got = symbols.enrich(base, demat=[_held()])
    assert got[0].series == "BE"
    assert got[0].kite_tradingsymbol == "HFCL-BE"
    assert got[0].kite_instrument_token == 548865


def test_results_are_sorted_by_symbol():
    base = symbols.from_instruments([
        _instrument(tradingsymbol="ZEEL"), _instrument(tradingsymbol="ACC")])
    assert [m.nse_symbol for m in symbols.enrich(base)] == ["ACC", "ZEEL"]


def test_a_holding_without_an_isin_does_not_erase_a_known_one():
    """Kite reports ISIN on every holding, but a blank must never overwrite a
    value the corporate-actions feed supplied."""
    base = symbols.from_instruments([_instrument()])
    got = symbols.enrich(base, isins={"HFCL": HFCL}, demat=[_held(isin="")])
    assert got[0].isin == HFCL


# ------------------------------------------------------------- coverage ---

def test_coverage_counts_identified_against_total():
    base = symbols.from_instruments([
        _instrument(tradingsymbol="HFCL"), _instrument(tradingsymbol="ACC")])
    got = symbols.enrich(base, isins={"HFCL": HFCL})
    assert symbols.coverage(got) == (1, 2)


def test_coverage_of_an_empty_map_is_zero_of_zero():
    assert symbols.coverage([]) == (0, 0)
