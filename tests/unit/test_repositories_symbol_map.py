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
    assert all(c.symbol == "HFCL" for c in changes)


def test_learning_an_isin_for_the_first_time_is_reported():
    """It changes how every later comparison treats the row -- it becomes
    eligible for ISIN matching -- so it is not a silent enrichment."""
    changes = diff(_mapping(isin=""), _mapping(isin=HFCL))
    assert [c.field for c in changes] == ["isin"]
    assert changes[0].was == ""
    assert changes[0].now == HFCL


def test_a_mapping_without_an_isin_is_not_identified():
    assert not _mapping(isin="").identified
    assert _mapping().identified


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


def test_a_rename_is_not_a_diff_because_it_is_a_different_row():
    """The symbol is the key now, so a rename writes a new document. What
    catches it is the unique partial index on ISIN -- two symbols claiming one
    ISIN is a collision, not a quiet update."""
    assert diff(_mapping(), _mapping(nse_symbol="HFCLNEW")) == []


# ------------------------------------------------------------- from_doc ---

def test_from_doc_reads_the_symbol_out_of_the_primary_key():
    """The key is the symbol now, because ISIN cannot always be obtained --
    the current session format carries none."""
    mapping = SymbolMapping.from_doc({"_id": "HFCL"})
    assert mapping.nse_symbol == "HFCL"


def test_from_doc_defaults_absent_optional_fields_to_empty():
    """A partially built map must not crash the resolver; it must resolve to
    something the caller can see is incomplete."""
    mapping = SymbolMapping.from_doc({"_id": "HFCL", "nse_symbol": "HFCL"})
    assert mapping.isin == ""
    assert mapping.feed_ticker == ""
    assert mapping.kite_tradingsymbol == ""
    assert mapping.kite_instrument_token is None


def test_a_mapping_change_stringifies_for_a_report():
    change = MappingChange(symbol="HFCL", field="series", was="EQ", now="BE")
    assert str(change) == "HFCL: series 'EQ' -> 'BE'"
