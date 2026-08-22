"""Tests for shadow-portfolio persistence (data/shadow/*), mirroring the
Parquet + manifest convention in algotrade.io.store."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import polars as pl

from algotrade.shadow import store

D = date


def test_weights_log_roundtrips(tmp_path: Path):
    weights = pl.DataFrame(
        {"date": [D(2026, 1, 1)], "symbol": ["TCS"], "weight": [1.0]}
    )
    manifest = store.write_weights_log(
        weights, directory=tmp_path, built_at="2026-01-01T00:00:00Z", commit="abc123"
    )
    assert manifest["commit"] == "abc123"
    assert manifest["sessions"] == 1

    back = store.read_weights_log(directory=tmp_path)
    assert back.equals(weights.select("date", "symbol", "weight"))


def test_reading_a_missing_weights_log_returns_empty_with_schema(tmp_path: Path):
    back = store.read_weights_log(directory=tmp_path)
    assert back.is_empty()
    assert back.schema == pl.Schema(
        {"date": pl.Date, "symbol": pl.String, "weight": pl.Float64}
    )


def test_equity_roundtrips(tmp_path: Path):
    equity = pl.DataFrame(
        {
            "date": [D(2026, 1, 1), D(2026, 1, 2)],
            "shadow_full_nav": [100_000.0, 100_500.0],
            "shadow_full_return": [0.0, 0.005],
            "benchmark_nav": [100_000.0, 100_200.0],
        }
    )
    store.write_equity(equity, directory=tmp_path)
    back = store.read_equity(directory=tmp_path)
    assert back.equals(equity)


def test_reading_missing_equity_returns_empty(tmp_path: Path):
    back = store.read_equity(directory=tmp_path)
    assert back.is_empty()


def test_append_jsonl_appends_one_record_per_call(tmp_path: Path):
    path = tmp_path / "run_log.jsonl"
    store.append_jsonl(path, {"session": "2026-01-01", "status": "ok"})
    store.append_jsonl(path, {"session": "2026-01-02", "status": "failed"})

    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "ok"
    assert json.loads(lines[1])["status"] == "failed"


def test_append_jsonl_creates_parent_directory(tmp_path: Path):
    path = tmp_path / "nested" / "run_log.jsonl"
    store.append_jsonl(path, {"a": 1})
    assert path.exists()
