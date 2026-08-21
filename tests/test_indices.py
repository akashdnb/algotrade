"""Tests for NSE index ingestion (PROJECT.md §11d)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from algotrade.io import indices

D = date

HEADER = (
    "Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,"
    "Closing Index Value,Points Change,Change(%),Volume,Turnover,P/E,P/B,Div Yield"
)


def write(directory: Path, day: date, rows: list[str]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / indices.filename_for(day)
    path.write_text(HEADER + "\n" + "\n".join(rows) + "\n")
    return path


def row(name: str, day: date, close: float, *, pe: str = "22.97") -> str:
    return (
        f"{name},{day:%d-%m-%Y},{close - 10},{close + 10},{close - 20},{close},"
        f"1.0,.05,242468081,\"28,204.06\",{pe},4.16,1.22"
    )


# --- parsing -----------------------------------------------------------------


def test_parses_canonical_schema(tmp_path: Path):
    p = write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 24141.95)])
    out = indices.read_file(p)
    assert out.columns == list(indices.SCHEMA)
    assert out["date"][0] == D(2024, 7, 1)
    assert out["close"][0] == pytest.approx(24141.95)


def test_strips_thousands_separators(tmp_path: Path):
    """Turnover arrives quoted with commas."""
    p = write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 100.0)])
    assert indices.read_file(p)["turnover"][0] == pytest.approx(28204.06)


def test_missing_pe_becomes_null_not_zero(tmp_path: Path):
    """A zero P/E is a real number and a wrong one; NSE writes '-'."""
    p = write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 100.0, pe="-")])
    assert indices.read_file(p)["pe"][0] is None


def test_every_index_in_the_file_is_kept(tmp_path: Path):
    p = write(
        tmp_path,
        D(2024, 7, 1),
        [
            row("Nifty 50", D(2024, 7, 1), 24000.0),
            row("Nifty Bank", D(2024, 7, 1), 52000.0),
            row("Nifty IT", D(2024, 7, 1), 36000.0),
        ],
    )
    assert indices.read_file(p).height == 3


# --- the rename problem, which is the point of this module -------------------


def test_benchmark_stitches_across_index_renames(tmp_path: Path):
    """The index was "S&P CNX Nifty", then "CNX Nifty", then "Nifty 50".

    Filtering on the current name alone returns nothing for the pre-2015 era and
    does so silently -- which is exactly how this was nearly missed.
    """
    write(tmp_path, D(2012, 3, 1), [row("S&P CNX Nifty", D(2012, 3, 1), 5600.0)])
    write(tmp_path, D(2014, 3, 3), [row("CNX Nifty", D(2014, 3, 3), 6200.0)])
    write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 24000.0)])

    got = indices.benchmark_series(tmp_path)
    assert got.height == 3
    assert got["close"].to_list() == pytest.approx([5600.0, 6200.0, 24000.0])


def test_current_name_alone_would_have_found_almost_nothing(tmp_path: Path):
    """Demonstrates the failure the alias list prevents."""
    write(tmp_path, D(2012, 3, 1), [row("S&P CNX Nifty", D(2012, 3, 1), 5600.0)])
    write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 24000.0)])
    assert indices.benchmark_series(tmp_path, aliases=["Nifty 50"]).height == 1
    assert indices.benchmark_series(tmp_path).height == 2


def test_overlapping_aliases_raise_rather_than_double_count(tmp_path: Path):
    """If two aliases report the same date the series is being double-counted
    through a naming overlap, not stitched across a rename."""
    write(
        tmp_path,
        D(2015, 11, 2),
        [
            row("CNX Nifty", D(2015, 11, 2), 8000.0),
            row("Nifty 50", D(2015, 11, 2), 8000.0),
        ],
    )
    with pytest.raises(ValueError, match="double-counted"):
        indices.benchmark_series(tmp_path)


# --- loading -----------------------------------------------------------------


def test_returns_daily_returns(tmp_path: Path):
    write(tmp_path, D(2024, 7, 1), [row("Nifty 50", D(2024, 7, 1), 100.0)])
    write(tmp_path, D(2024, 7, 2), [row("Nifty 50", D(2024, 7, 2), 110.0)])
    got = indices.benchmark_series(tmp_path)
    assert got["ret"][0] is None  # no prior close
    assert got["ret"][1] == pytest.approx(0.10)


def test_date_range_filter(tmp_path: Path):
    for day in (D(2024, 7, 1), D(2024, 7, 2), D(2024, 7, 3)):
        write(tmp_path, day, [row("Nifty 50", day, 100.0)])
    got = indices.benchmark_series(tmp_path, start=D(2024, 7, 2), end=D(2024, 7, 2))
    assert got.height == 1


def test_parse_failures_raise_past_the_limit(tmp_path: Path):
    """An earlier version swallowed these, which would hide a systematic
    problem behind a plausible-looking short series."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    for i in range(1, 26):
        (tmp_path / indices.filename_for(D(2024, 7, i))).write_bytes(b"\x00\x01\x02")
    with pytest.raises(ValueError, match="failed to parse"):
        indices.load(tmp_path, max_failures=20)


def test_empty_directory_returns_empty(tmp_path: Path):
    assert indices.benchmark_series(tmp_path).is_empty()


def test_filename_convention():
    assert indices.filename_for(D(2024, 7, 1)) == "ind_close_all_01072024.csv"
