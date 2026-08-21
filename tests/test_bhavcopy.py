"""Regression tests for bhavcopy parsing.

Every test here corresponds to a trap found by auditing real NSE data
(PROJECT.md §10.3). They are regression tests in the strict sense: each one
failed against a naive parser before the fix existed.

Fixtures are real bhavcopy files chosen for the edge case each contains.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from algotrade.io.bhavcopy import (
    SCHEMA,
    BhavcopyError,
    date_from_filename,
    read_file,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Old era: no whitespace, delivery/VWAP zero-filled.
F_2018 = FIXTURES / "sec_bhavdata_full_01012018.csv"
# New era: leading whitespace in header and values, delivery populated.
F_2024 = FIXTURES / "sec_bhavdata_full_01072024.csv"
# cm era: carries ISIN, turnover already in rupees.
F_CM = FIXTURES / "cm01APR2015bhav.csv"


def test_schema_is_stable_across_eras():
    """Both formats must yield byte-identical column names and dtypes."""
    for path in (F_2018, F_2024, F_CM):
        df = read_file(path)
        assert df.columns == list(SCHEMA), path.name
        assert dict(zip(df.columns, df.dtypes)) == SCHEMA, path.name


def test_whitespace_era_parses():
    """Later files carry ' EQ' and ' 01-Jul-2024'. Unstripped, this yields zero rows."""
    df = read_file(F_2024)
    assert df.height > 1000
    assert df["date"][0] == date(2024, 7, 1)
    assert set(df["series"].unique()) == {"EQ"}


def test_series_filter_excludes_debt_series():
    """DHFL appears 8x on 2018-01-01: once as EQ, seven times as NCDs.

    Unfiltered, debt instruments get treated as equity.
    """
    all_series = read_file(F_2018, series=None).filter(pl.col("symbol") == "DHFL")
    eq_only = read_file(F_2018).filter(pl.col("symbol") == "DHFL")
    assert all_series.height == 8
    assert eq_only.height == 1
    assert eq_only["series"][0] == "EQ"


def test_zero_filled_columns_become_null():
    """NSE zero-fills VWAP/trades/delivery in backfilled files.

    Zero must not be read as a real observation, or delivery-based signals
    would see a decade of legitimate-looking zeros.
    """
    df = read_file(F_2018)
    for col in ("vwap", "trades", "deliv_qty", "deliv_pct"):
        assert df[col].null_count() == df.height, col
    # Genuinely present columns are untouched.
    assert df["close"].null_count() == 0


def test_genuine_values_preserved_when_column_is_populated():
    """The blanking rule is per file, not per row: real data must survive."""
    df = read_file(F_2024)
    for col in ("vwap", "trades", "deliv_qty", "deliv_pct"):
        assert df[col].null_count() < df.height, col
    reliance = df.filter(pl.col("symbol") == "RELIANCE")
    assert reliance["deliv_pct"][0] == pytest.approx(53.99)
    assert reliance["vwap"][0] == pytest.approx(3126.53)


def test_turnover_normalised_to_rupees_in_both_eras():
    """TURNOVER_LACS is lakhs, TOTTRDVAL is rupees. Both must end up in rupees.

    Cross-checked against volume x close, which should agree to a few percent
    (they differ only because close != VWAP).
    """
    for path in (F_2024, F_CM):
        row = read_file(path).filter(pl.col("symbol") == "RELIANCE")
        implied = row["volume"][0] * row["close"][0]
        assert row["turnover"][0] == pytest.approx(implied, rel=0.05), path.name


def test_isin_present_only_in_cm_era():
    """ISIN exists in cm*bhav but not sec_bhavdata_full -- the reason §10.7
    reconstructs identity from PREV_CLOSE continuity for recent years."""
    assert read_file(F_CM)["isin"].null_count() == 0
    assert read_file(F_2024)["isin"].null_count() == read_file(F_2024).height


def test_delisted_names_are_retained():
    """The survivorship-bias check. These four all later delisted or were
    suspended; a date-organised archive must still carry them in 2018."""
    df = read_file(F_2018)
    present = set(df["symbol"].to_list())
    for symbol in ("JETAIRWAYS", "DHFL", "GITANJALI", "MANPASAND"):
        assert symbol in present, symbol


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("sec_bhavdata_full_05082022.csv", date(2022, 8, 5)),
        ("sec_bhavdata_full_31122011.csv", date(2011, 12, 31)),
        ("cm01APR2015bhav.csv", date(2015, 4, 1)),
        ("cm09NOV2020bhav.csv", date(2020, 11, 9)),
    ],
)
def test_date_from_filename(name: str, expected: date):
    assert date_from_filename(name) == expected


def test_date_from_filename_rejects_unknown():
    with pytest.raises(BhavcopyError, match="unrecognised"):
        date_from_filename("bhavcopy_2020.csv")


def test_filename_content_date_mismatch_raises(tmp_path: Path):
    """A file whose rows disagree with its filename is corrupt, not merely odd.

    Guards against the archive mislabelling a date, which would silently shift
    a whole session in the panel.
    """
    bad = tmp_path / "sec_bhavdata_full_01072024.csv"
    bad.write_text(
        "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,"
        " LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS,"
        " NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"
        "FOO, EQ, 02-Jul-2024, 10, 10, 11, 9, 10, 10, 10, 100, 1, 5, 50, 50.0\n"
    )
    with pytest.raises(BhavcopyError, match="filename says"):
        read_file(bad)


def test_unrecognised_layout_raises(tmp_path: Path):
    bad = tmp_path / "sec_bhavdata_full_01072024.csv"
    bad.write_text("FOO,BAR\n1,2\n")
    with pytest.raises(BhavcopyError, match="neither DATE1 nor TIMESTAMP"):
        read_file(bad)
