"""Tests for the curated Parquet store and the adjusted-panel cache."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from algotrade.io import store

D = date


def panel(days: list[date], symbols: tuple[str, ...] = ("A", "B")) -> pl.DataFrame:
    return pl.DataFrame(
        [(d, s, 100.0) for d in days for s in symbols],
        schema={"date": pl.Date, "symbol": pl.String, "close": pl.Float64},
        orient="row",
    )


def factors(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"symbol": pl.String, "ex_date": pl.Date, "factor": pl.Float64},
        orient="row",
    )


# --- roundtrip ---------------------------------------------------------------


def test_roundtrip_preserves_rows_and_dtypes(tmp_path: Path):
    original = panel([D(2023, 6, 1), D(2024, 6, 1)])
    store.write(original, directory=tmp_path)
    back = store.read(tmp_path)
    assert back.height == original.height
    assert dict(zip(back.columns, back.dtypes)) == dict(
        zip(original.columns, original.dtypes)
    )


def test_partitioned_by_year(tmp_path: Path):
    store.write(panel([D(2022, 1, 3), D(2023, 1, 3), D(2024, 1, 3)]), directory=tmp_path)
    years = sorted(p.name for p in tmp_path.glob("year=*"))
    assert years == ["year=2022", "year=2023", "year=2024"]


def test_date_range_read(tmp_path: Path):
    store.write(panel([D(2022, 5, 1), D(2023, 5, 1), D(2024, 5, 1)]), directory=tmp_path)
    got = store.read(tmp_path, start=D(2023, 1, 1), end=D(2023, 12, 31))
    assert got["date"].unique().to_list() == [D(2023, 5, 1)]


def test_column_projection(tmp_path: Path):
    store.write(panel([D(2024, 1, 1)]), directory=tmp_path)
    assert store.read(tmp_path, columns=["date", "close"]).columns == ["date", "close"]


def test_rebuild_removes_stale_partitions(tmp_path: Path):
    """A shorter rebuild must not leave last run's years behind."""
    store.write(panel([D(2022, 1, 3), D(2023, 1, 3)]), directory=tmp_path)
    store.write(panel([D(2023, 1, 3)]), directory=tmp_path)
    assert sorted(p.name for p in tmp_path.glob("year=*")) == ["year=2023"]


def test_read_without_a_build_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="no curated panel"):
        store.read(tmp_path)


# --- manifest ----------------------------------------------------------------


def test_manifest_records_inputs(tmp_path: Path):
    """Git versions code, not data. Without this, a rerun after a backfill
    silently produces different numbers from identical code."""
    manifest = store.write(
        panel([D(2024, 1, 1), D(2024, 1, 2)]),
        directory=tmp_path,
        rejected=[D(2024, 1, 3)],
        source_dirs=["data/raw/mirror"],
        built_at="2026-08-21T00:00:00+00:00",
    )
    assert manifest.sessions == 2
    assert manifest.rows == 4
    assert manifest.symbols == 2
    assert manifest.rejected_sessions == ["2024-01-03"]

    on_disk = store.read_manifest(tmp_path)
    assert on_disk["first_session"] == "2024-01-01"
    assert on_disk["source_dirs"] == ["data/raw/mirror"]
    assert on_disk["built_at"] == "2026-08-21T00:00:00+00:00"


# --- adjusted cache: fingerprinting -----------------------------------------


def test_fingerprint_is_order_independent():
    a = factors([("A", D(2024, 1, 1), 2.0), ("B", D(2024, 2, 1), 5.0)])
    b = factors([("B", D(2024, 2, 1), 5.0), ("A", D(2024, 1, 1), 2.0)])
    assert store.factor_fingerprint(a) == store.factor_fingerprint(b)


def test_fingerprint_changes_with_any_factor():
    base = factors([("A", D(2024, 1, 1), 2.0)])
    for variant in (
        factors([("A", D(2024, 1, 1), 3.0)]),  # factor changed
        factors([("A", D(2024, 1, 2), 2.0)]),  # date changed
        factors([("Z", D(2024, 1, 1), 2.0)]),  # symbol changed
    ):
        assert store.factor_fingerprint(variant) != store.factor_fingerprint(base)


def test_adjusted_cache_roundtrips(tmp_path: Path):
    fac = factors([("A", D(2024, 1, 2), 2.0)])
    adjusted = panel([D(2024, 1, 1), D(2024, 1, 2)])
    store.write_adjusted(adjusted, fac, directory=tmp_path, excluded=["PEL"])
    got = store.read_adjusted(fac, directory=tmp_path)
    assert got is not None
    assert got.height == adjusted.height


def test_stale_adjusted_cache_returns_none(tmp_path: Path):
    """The whole point: a panel adjusted by different factors must never be
    served silently. Returning None makes staleness a rebuild trigger."""
    built_with = factors([("A", D(2024, 1, 2), 2.0)])
    store.write_adjusted(panel([D(2024, 1, 1)]), built_with, directory=tmp_path)

    changed = factors([("A", D(2024, 1, 2), 10.0)])
    assert store.read_adjusted(changed, directory=tmp_path) is None


def test_missing_adjusted_cache_returns_none(tmp_path: Path):
    assert store.read_adjusted(factors([]), directory=tmp_path) is None


def test_adjusted_manifest_records_exclusions(tmp_path: Path):
    fac = factors([("A", D(2024, 1, 2), 2.0)])
    info = store.write_adjusted(
        panel([D(2024, 1, 1)]),
        fac,
        directory=tmp_path,
        excluded=["SONAMCLOCK", "PEL"],
        built_at="2026-08-21T00:00:00+00:00",
    )
    assert info["factors_applied"] == 1
    assert info["excluded_symbols"] == ["PEL", "SONAMCLOCK"]
    assert info["factor_fingerprint"] == store.factor_fingerprint(fac)
