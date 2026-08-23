"""Tests for multi-directory archive loading and NSE backfill decoding.

No network access: the NSE tests exercise `_decode`, which is where the actual
complexity lives (NSE serves XLSX bodies under `.csv` URLs).
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from algotrade.io import nse
from algotrade.io.archive import discover, load_panel
from algotrade.io.bhavcopy import read_file

HEADER = (
    "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE,"
    " LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS,"
    " NO_OF_TRADES, DELIV_QTY, DELIV_PER"
)


def session_csv(day: str, close: float = 100.0, prev: float = 99.0) -> str:
    return (
        f"{HEADER}\n"
        f"FOO, EQ, {day}, {prev}, {prev}, {close}, {prev}, {close}, {close},"
        f" {close}, 1000, 10.5, 25, 500, 50.0\n"
    )


def write_session(directory: Path, stamp: str, day: str, **kw) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"sec_bhavdata_full_{stamp}.csv"
    path.write_text(session_csv(day, **kw))
    return path


# --- archive discovery -------------------------------------------------------


def test_discover_maps_dates_to_files(tmp_path: Path):
    write_session(tmp_path / "mirror", "01072024", "01-Jul-2024")
    write_session(tmp_path / "mirror", "02072024", "02-Jul-2024")
    found = discover([tmp_path / "mirror"])
    assert set(found) == {date(2024, 7, 1), date(2024, 7, 2)}


def test_later_directory_wins(tmp_path: Path):
    """A backfilled session must override a stale mirrored one of the same date."""
    write_session(tmp_path / "mirror", "01072024", "01-Jul-2024", close=1.0)
    write_session(tmp_path / "backfill", "01072024", "01-Jul-2024", close=2.0)
    found = discover([tmp_path / "mirror", tmp_path / "backfill"])
    assert found[date(2024, 7, 1)].parent.name == "backfill"


def test_missing_directory_is_ignored(tmp_path: Path):
    write_session(tmp_path / "mirror", "01072024", "01-Jul-2024")
    found = discover([tmp_path / "mirror", tmp_path / "does-not-exist"])
    assert len(found) == 1


def test_non_bhavcopy_files_ignored(tmp_path: Path):
    d = tmp_path / "mirror"
    write_session(d, "01072024", "01-Jul-2024")
    (d / "notes.csv").write_text("hello\n")
    assert len(discover([d])) == 1


# --- archive loading ---------------------------------------------------------


def test_load_panel_concatenates_and_sorts(tmp_path: Path):
    d = tmp_path / "mirror"
    write_session(d, "02072024", "02-Jul-2024")
    write_session(d, "01072024", "01-Jul-2024")
    panel, report = load_panel([d])
    assert report.sessions_loaded == 2
    assert panel["date"].to_list() == [date(2024, 7, 1), date(2024, 7, 2)]


def test_load_panel_records_stale_duplicates(tmp_path: Path):
    """The mirror's holiday artefacts must be skipped and reported, not fatal.

    Mirrors the real defect: sec_bhavdata_full_15082022.csv holds 12-Aug data.
    """
    d = tmp_path / "mirror"
    write_session(d, "12082022", "12-Aug-2022")
    write_session(d, "15082022", "12-Aug-2022")  # stale duplicate
    panel, report = load_panel([d])
    assert report.sessions_loaded == 1
    assert report.rejected_dates == [date(2022, 8, 15)]
    assert panel["date"].unique().to_list() == [date(2022, 8, 12)]


def test_load_panel_date_range(tmp_path: Path):
    d = tmp_path / "mirror"
    for stamp, day in [
        ("01072024", "01-Jul-2024"),
        ("02072024", "02-Jul-2024"),
        ("03072024", "03-Jul-2024"),
    ]:
        write_session(d, stamp, day)
    panel, report = load_panel(
        [d], start=date(2024, 7, 2), end=date(2024, 7, 2)
    )
    assert report.sessions_loaded == 1
    assert panel["date"][0] == date(2024, 7, 2)


def test_load_panel_reports_superseded(tmp_path: Path):
    write_session(tmp_path / "mirror", "01072024", "01-Jul-2024")
    write_session(tmp_path / "backfill", "01072024", "01-Jul-2024")
    _, report = load_panel([tmp_path / "mirror", tmp_path / "backfill"])
    assert date(2024, 7, 1) in report.superseded


def test_load_panel_empty_when_nothing_matches(tmp_path: Path):
    panel, report = load_panel([tmp_path / "nope"])
    assert panel.is_empty()
    assert report.sessions_loaded == 0


# --- NSE response decoding ---------------------------------------------------


def test_decode_plain_csv():
    body = session_csv("01-Jul-2024").encode()
    assert "FOO" in nse._decode(body)


def test_decode_zipped_csv():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("sec_bhavdata_full_01072024.csv", session_csv("01-Jul-2024"))
    assert "FOO" in nse._decode(buf.getvalue())


def test_decode_xlsx_disguised_as_csv(tmp_path: Path):
    """NSE serves XLSX bodies from `.csv` URLs -- the observed real behaviour.

    This is what made nsefin's fetch raise UnicodeDecodeError.
    """
    import pandas as pd

    frame = pd.DataFrame(
        [["FOO", "EQ", "08-Aug-2022", 99.0, 99.0, 100.0, 99.0, 100.0, 100.0,
          100.0, 1000, 10.5, 25, 500, 50.0]],
        columns=[c.strip() for c in HEADER.split(",")],
    )
    book = tmp_path / "b.xlsx"
    frame.to_excel(book, index=False)
    text = nse._decode(book.read_bytes())
    assert "FOO" in text
    assert "08-Aug-2022" in text


def test_decode_rejects_zip_without_csv():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("readme.txt", "nope")
    with pytest.raises(nse.NseFetchError, match="no csv or workbook"):
        nse._decode(buf.getvalue())


def test_filename_matches_mirror_convention():
    assert nse.filename_for(date(2022, 8, 8)) == "sec_bhavdata_full_08082022.csv"


def test_backfilled_xlsx_roundtrips_through_the_parser(tmp_path: Path):
    """End-to-end: an XLSX body must land as a file the parser reads normally.

    Excel stores numbers as floats, so volume arrives as "1000.0"; a direct
    Int64 cast would silently null it.
    """
    import pandas as pd

    frame = pd.DataFrame(
        [["FOO", "EQ", "08-Aug-2022", 99.0, 99.0, 100.0, 99.0, 100.0, 100.0,
          100.0, 1000, 10.5, 25, 500, 50.0]],
        columns=[c.strip() for c in HEADER.split(",")],
    )
    book = tmp_path / "raw.xlsx"
    frame.to_excel(book, index=False)

    out = tmp_path / "sec_bhavdata_full_08082022.csv"
    out.write_text(nse._decode(book.read_bytes()))

    df = read_file(out)
    assert df.height == 1
    assert df["volume"][0] == 1000
    assert df["volume"].dtype == pl.Int64
    assert df["turnover"][0] == pytest.approx(10.5 * 100_000)
    assert df["date"][0] == date(2022, 8, 8)


def test_discover_finds_year_partitioned_sessions(tmp_path):
    """The forward store is `data/sessions/YYYY/`; the research mirror is flat.
    A non-recursive glob found zero files in the partitioned layout, which
    would have produced an empty panel in CI -- where only the partitioned
    store exists."""
    from algotrade.io.archive import discover

    flat = tmp_path / "mirror"
    flat.mkdir()
    (flat / "sec_bhavdata_full_20082026.csv").write_text("x")

    nested = tmp_path / "sessions"
    (nested / "2026").mkdir(parents=True)
    (nested / "2026" / "sec_bhavdata_full_19082026.csv").write_text("x")

    found = discover([flat, nested])
    assert date(2026, 8, 20) in found, "flat layout must still work"
    assert date(2026, 8, 19) in found, "year-partitioned layout must be found"
