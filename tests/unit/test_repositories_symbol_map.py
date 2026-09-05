"""Tests for symbol identity (design §6).

The pure parts: what counts as a change, and what `from_doc` makes of a
document. Storage and the unique index are covered in the integration suite.

The case that matters is the EQ->BE move. HFCL's tradingsymbol changed while
the ISIN did not, which is precisely what the map exists to absorb -- and
precisely what a string-matching fallback would get wrong.
"""

from __future__ import annotations

from algotrade.repositories.symbol_map import (
    MappingChange,
    SymbolMapping,
    diff,
)

HFCL = "INE548A01028"


def _mapping(**kwargs) -> SymbolMapping:
    args = dict(isin=HFCL, nse_symbol="HFCL", feed_ticker="HFCL.NS",
                kite_tradingsymbol="HFCL", kite_instrument_token=548865,
                series="EQ")
    args.update(kwargs)
    return SymbolMapping(**args)


# ----------------------------------------------------------------- diff ---

def test_an_unchanged_mapping_reports_nothing():
    assert diff(_mapping(), _mapping()) == []


def test_a_series_move_is_reported_field_by_field():
    """HFCL EQ->BE: the series and the tradingsymbol move, the ISIN does not."""
    changes = diff(_mapping(),
                   _mapping(series="BE", kite_tradingsymbol="HFCL-BE"))
    assert {c.field for c in changes} == {"series", "kite_tradingsymbol"}
    assert all(c.isin == HFCL for c in changes)


def test_a_change_carries_both_the_old_and_new_value():
    change = diff(_mapping(), _mapping(series="BE"))[0]
    assert change.was == "EQ"
    assert change.now == "BE"
    assert "'EQ' -> 'BE'" in str(change)


def test_an_instrument_token_change_is_reported():
    """Kite reissues tokens on a series move; a stale one addresses nothing."""
    changes = diff(_mapping(), _mapping(kite_instrument_token=999999))
    assert [c.field for c in changes] == ["kite_instrument_token"]


def test_updated_on_is_not_tracked_as_a_change():
    """It moves on every refresh, so tracking it would make every symbol a
    finding and drown the ones that matter."""
    changes = diff(_mapping(), _mapping())
    assert not any(c.field == "updated_on" for c in changes)


def test_a_renamed_symbol_keeps_its_isin_identity():
    changes = diff(_mapping(), _mapping(nse_symbol="HFCLNEW"))
    assert [c.field for c in changes] == ["nse_symbol"]
    assert changes[0].isin == HFCL


# ------------------------------------------------------------- from_doc ---

def test_from_doc_reads_the_isin_out_of_the_primary_key():
    mapping = SymbolMapping.from_doc({"_id": HFCL, "nse_symbol": "HFCL"})
    assert mapping.isin == HFCL
    assert mapping.nse_symbol == "HFCL"


def test_from_doc_defaults_absent_optional_fields_to_empty():
    """A partially built map must not crash the resolver; it must simply
    resolve to something the caller can see is incomplete."""
    mapping = SymbolMapping.from_doc({"_id": HFCL, "nse_symbol": "HFCL"})
    assert mapping.feed_ticker == ""
    assert mapping.kite_tradingsymbol == ""
    assert mapping.kite_instrument_token is None


def test_a_mapping_change_stringifies_for_a_report():
    change = MappingChange(isin=HFCL, field="series", was="EQ", now="BE")
    assert str(change) == f"{HFCL}: series 'EQ' -> 'BE'"
