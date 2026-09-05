"""Tests for assembling symbol identity (design §6).

Three properties matter here, and each is a decision rather than a mechanism:

* the map holds only instruments this system can actually trade;
* the broker outranks the corporate-actions feed on ISIN;
* a symbol with no tradingsymbol is skipped, never half-written.
"""

from __future__ import annotations

from decimal import Decimal

from algotrade.brokers.base import BrokerHolding
from algotrade.services import symbols

HFCL = "INE548A01028"
RELIANCE = "INE002A01018"


def _instrument(**kwargs) -> dict:
    row = {"exchange": "NSE", "instrument_type": "EQ",
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
    """`M-M` is a real NSE symbol. Treating `M` as its base would map two
    different companies onto one row."""
    got = symbols.from_instruments([_instrument(tradingsymbol="M-M")])
    assert set(got) == {"M-M"}
    assert got["M-M"].series == "EQ"


def test_other_exchanges_are_excluded():
    assert symbols.from_instruments([_instrument(exchange="BSE")]) == {}


def test_derivatives_and_other_types_are_excluded():
    """The dump carries futures, options, currency and commodities, none of
    which this system can trade."""
    assert symbols.from_instruments([_instrument(instrument_type="FUT")]) == {}


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
