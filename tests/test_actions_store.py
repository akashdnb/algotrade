"""Tests for the corporate-action cache and the ISIN rename bridge."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from algotrade.core import adjust as adj
from algotrade.core import reconcile as rc
from algotrade.io import actions as store

D = date


def write_quarter(directory: Path, year: int, quarter: int, rows: list[dict]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    store.cache_path(directory, year, quarter).write_text(json.dumps(rows))


def action(symbol: str, ex: str, subject: str = "Bonus 1:1", isin: str = "") -> dict:
    return {"symbol": symbol, "exDate": ex, "subject": subject, "isin": isin,
            "faceVal": "1"}


# --- quarter arithmetic ------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (D(2022, 1, 1), (2022, 1)),
        (D(2022, 3, 31), (2022, 1)),
        (D(2022, 4, 1), (2022, 2)),
        (D(2022, 12, 31), (2022, 4)),
    ],
)
def test_quarter_of(day: date, expected: tuple[int, int]):
    assert store.quarter_of(day) == expected


def test_quarter_bounds_handles_year_end():
    assert store.quarter_bounds(2022, 4) == (D(2022, 10, 1), D(2022, 12, 31))
    assert store.quarter_bounds(2022, 1) == (D(2022, 1, 1), D(2022, 3, 31))


def test_quarters_between_spans_years():
    got = store.quarters_between(D(2021, 11, 1), D(2022, 2, 1))
    assert got == [(2021, 4), (2022, 1)]


# --- loading -----------------------------------------------------------------


def test_load_merges_quarters_and_parses_dates(tmp_path: Path):
    write_quarter(tmp_path, 2022, 1, [action("A", "15-Feb-2022")])
    write_quarter(tmp_path, 2022, 2, [action("B", "10-May-2022")])
    frame = store.load(tmp_path)
    assert frame.height == 2
    assert frame["ex_date"].to_list() == [D(2022, 2, 15), D(2022, 5, 10)]


def test_load_dedupes_across_window_boundaries(tmp_path: Path):
    """NSE repeats rows when windows overlap."""
    row = action("A", "15-Feb-2022")
    write_quarter(tmp_path, 2022, 1, [row, dict(row)])
    assert store.load(tmp_path).height == 1


def test_load_drops_unparseable_ex_dates(tmp_path: Path):
    """A null ex-date cannot be reconciled against anything."""
    write_quarter(tmp_path, 2022, 1, [action("A", "15-Feb-2022"), action("B", "")])
    assert store.load(tmp_path)["symbol"].to_list() == ["A"]


def test_load_date_range(tmp_path: Path):
    write_quarter(tmp_path, 2022, 1, [action("A", "15-Feb-2022")])
    write_quarter(tmp_path, 2022, 2, [action("B", "10-May-2022")])
    got = store.load(tmp_path, start=D(2022, 4, 1))
    assert got["symbol"].to_list() == ["B"]


def test_load_empty_directory(tmp_path: Path):
    assert store.load(tmp_path).is_empty()


# --- sync bookkeeping (no network) -------------------------------------------


def test_sync_reuses_cached_quarters(tmp_path: Path, monkeypatch):
    """A cached quarter must not be refetched -- NSE is not a dependency to
    hammer during research."""
    write_quarter(tmp_path, 2022, 1, [action("A", "15-Feb-2022")])

    def boom(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("should not fetch a cached quarter")

    monkeypatch.setattr(store.nse, "fetch_corporate_actions", boom)
    monkeypatch.setattr(store.nse, "_session", lambda: None)
    report = store.sync(
        D(2022, 1, 1), D(2022, 3, 31), directory=tmp_path, today=D(2024, 1, 1)
    )
    assert report.reused == ["2022Q1"]
    assert report.fetched == []


def test_sync_refetches_the_current_quarter(tmp_path: Path, monkeypatch):
    """The live quarter is still accumulating, so a cached copy is stale."""
    write_quarter(tmp_path, 2024, 1, [action("A", "15-Feb-2024")])
    monkeypatch.setattr(
        store.nse, "fetch_corporate_actions", lambda *a, **k: [action("B", "01-Mar-2024")]
    )
    monkeypatch.setattr(store.nse, "_session", lambda: None)
    report = store.sync(
        D(2024, 1, 1), D(2024, 3, 31), directory=tmp_path, today=D(2024, 2, 15)
    )
    assert report.fetched == ["2024Q1"]


def test_sync_records_failures_and_continues(tmp_path: Path, monkeypatch):
    """One bad window must not lose a 65-call run."""
    calls = {"n": 0}

    def flaky(start, end, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise store.nse.NseFetchError("boom")
        return [action("B", "10-May-2022")]

    monkeypatch.setattr(store.nse, "fetch_corporate_actions", flaky)
    monkeypatch.setattr(store.nse, "_session", lambda: None)
    report = store.sync(
        D(2022, 1, 1), D(2022, 6, 30), directory=tmp_path, today=D(2024, 1, 1)
    )
    assert list(report.failed) == ["2022Q1"]
    assert report.fetched == ["2022Q2"]


# --- ISIN bridge -------------------------------------------------------------


def test_isin_map_carries_latest_ex_date(tmp_path: Path):
    write_quarter(
        tmp_path,
        2022,
        1,
        [
            action("OLD", "15-Jan-2022", isin="INE001"),
            action("OLD", "15-Feb-2022", isin="INE001"),
        ],
    )
    got = store.isin_map(store.load(tmp_path))
    assert got.height == 1
    assert got["last_ex_date"][0] == D(2022, 2, 15)


def test_chains_from_isin_orients_by_latest_ex_date():
    """The feed reports today's symbol, so the latest ex-date is the current one."""
    isin_map = pl.DataFrame(
        {
            "symbol": ["OLDNAME", "NEWNAME"],
            "isin": ["INE001", "INE001"],
            "last_ex_date": [D(2021, 5, 1), D(2024, 5, 1)],
        }
    )
    chains = rc.chains_from_isin(isin_map)
    assert chains.height == 1
    row = chains.row(0, named=True)
    assert row["historical_symbol"] == "OLDNAME"
    assert row["current_symbol"] == "NEWNAME"


def test_chains_from_isin_ignores_unshared_isins():
    isin_map = pl.DataFrame(
        {
            "symbol": ["A", "B"],
            "isin": ["INE001", "INE002"],
            "last_ex_date": [D(2022, 1, 1), D(2022, 1, 1)],
        }
    )
    assert rc.chains_from_isin(isin_map).is_empty()


def test_chains_from_isin_requires_columns():
    with pytest.raises(ValueError, match="missing columns"):
        rc.chains_from_isin(pl.DataFrame({"symbol": ["A"]}))


def test_merge_chains_prefers_the_first_source():
    """Price continuity is stronger evidence than a shared ISIN."""
    price = pl.DataFrame(
        {"historical_symbol": ["X"], "current_symbol": ["FROM_PRICE"]}
    )
    isin = pl.DataFrame(
        {"historical_symbol": ["X", "Y"], "current_symbol": ["FROM_ISIN", "Z"]}
    )
    merged = rc.merge_chains(price, isin)
    got = dict(
        zip(merged["historical_symbol"].to_list(), merged["current_symbol"].to_list())
    )
    assert got == {"X": "FROM_PRICE", "Y": "Z"}


# --- window-based exclusion --------------------------------------------------


def test_windows_pad_around_the_event():
    result = {
        "move_no_action": pl.DataFrame(
            {"date": [D(2022, 8, 30)], "symbol": ["PEL"], "implied_ratio": [2.5]}
        )
    }
    got = adj.unadjusted_windows(result)
    assert got["date"].to_list() == [D(2022, 8, 29), D(2022, 8, 30), D(2022, 8, 31)]


def test_genuine_moves_below_the_threshold_are_kept():
    """1,093 of 1,800 unexplained moves sit in the 1.25-1.45 band. Circuit
    limits reach 20% and illiquid names move further, so treating those as data
    errors would discard real returns."""
    result = {
        "move_no_action": pl.DataFrame(
            {
                "date": [D(2022, 1, 1), D(2022, 1, 2)],
                "symbol": ["GENUINE", "SUSPICIOUS"],
                "implied_ratio": [1.35, 2.0],
            }
        )
    }
    assert adj.unadjusted_windows(result)["symbol"].unique().to_list() == [
        "SUSPICIOUS"
    ]


def test_drop_windows_removes_only_flagged_rows():
    panel = pl.DataFrame(
        {
            "symbol": ["A", "A", "B"],
            "date": [D(2022, 1, 1), D(2022, 1, 2), D(2022, 1, 1)],
            "close": [1.0, 2.0, 3.0],
        }
    )
    windows = pl.DataFrame({"symbol": ["A"], "date": [D(2022, 1, 2)]})
    got = adj.drop_windows(panel, windows)
    assert got.height == 2
    assert D(2022, 1, 2) not in got.filter(pl.col("symbol") == "A")["date"].to_list()


def test_drop_windows_is_a_noop_when_clean():
    panel = pl.DataFrame({"symbol": ["A"], "date": [D(2022, 1, 1)], "close": [1.0]})
    assert adj.drop_windows(panel, pl.DataFrame()).height == 1
