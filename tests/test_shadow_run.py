"""Integration tests for the shadow-portfolio nightly orchestrator.

`sync_fn`, `signal_fn`, `index_returns_fn`, and `alert_fn` are injected so
these tests exercise `run_once`'s branching (weekend/holiday/failure
handling, parity detection, baseline advancement, alerting) without a real
network call, real bhavcopy files, or a real Slack push -- each of the
pieces `sync_fn` would otherwise call (fetch, load_panel, store.write) is
already covered by test_archive_and_nse.py / test_bhavcopy.py / test_store.py.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import polars as pl
import pytest

from algotrade.shadow import store as shadow_store
from scripts import shadow_run

D = date


def _next_weekend_day() -> date:
    d = D(2026, 1, 1)
    while d.weekday() < 5:
        d += timedelta(days=1)
    return d


def _panel(days: list) -> pl.DataFrame:
    rows = []
    for i, d in enumerate(days):
        rows.append({"date": d, "symbol": "AAA", "open": 100.0 + i})
        rows.append({"date": d, "symbol": "BBB", "open": 50.0 + i * 0.5})
    return pl.DataFrame(rows)


def _weights(days: list, aaa: float, bbb: float) -> pl.DataFrame:
    rows = []
    for d in days:
        rows.append({"date": d, "symbol": "AAA", "weight": aaa})
        rows.append({"date": d, "symbol": "BBB", "weight": bbb})
    return pl.DataFrame(rows).cast({"date": pl.Date, "symbol": pl.String, "weight": pl.Float64})


class Recorder:
    def __init__(self):
        self.calls: list = []

    def __call__(self, *args):
        self.calls.append(args)


def test_weekend_is_skipped_without_syncing_or_alerting(tmp_path: Path):
    weekend = _next_weekend_day()
    sync_calls = Recorder()
    alert_calls = Recorder()

    record = shadow_run.run_once(
        today=weekend,
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: (sync_calls(day), ("ok", None, ""))[1],
        alert_fn=alert_calls,
    )

    assert record["status"] == "skipped: weekend"
    assert sync_calls.calls == []
    assert alert_calls.calls == []
    log = (tmp_path / "run_log.jsonl").read_text().strip().splitlines()
    assert len(log) == 1


def test_holiday_is_skipped_without_alerting(tmp_path: Path):
    weekday = D(2026, 1, 5)  # a Monday
    assert weekday.weekday() < 5
    alert_calls = Recorder()

    record = shadow_run.run_once(
        today=weekday,
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: ("holiday", None, "404: not a trading session"),
        alert_fn=alert_calls,
    )

    assert record["status"] == "skipped: no data (holiday)"
    assert alert_calls.calls == []


def test_fetch_failure_alerts_and_records_detail(tmp_path: Path):
    weekday = D(2026, 1, 5)
    alert_calls = Recorder()

    record = shadow_run.run_once(
        today=weekday,
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: ("failed", None, "ConnectionError: network down"),
        alert_fn=alert_calls,
    )

    assert record["status"] == "failed: fetch"
    assert "network down" in record["detail"]
    assert len(alert_calls.calls) == 1
    assert "network down" in alert_calls.calls[0][0]


def test_first_ever_run_has_no_discrepancies_and_persists_baseline(tmp_path: Path):
    days = [D(2026, 1, 1) + timedelta(days=i) for i in range(6)]
    panel = _panel(days)
    weights = _weights(days, 0.6, 0.4)
    alert_calls = Recorder()

    record = shadow_run.run_once(
        today=days[-1],
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: ("ok", panel, ""),
        signal_fn=lambda p: weights,
        index_returns_fn=lambda day: pl.DataFrame(
            {"date": days, "ret": [0.0] + [0.001] * (len(days) - 1)}
        ),
        alert_fn=alert_calls,
    )

    assert record["status"] == "ok"
    assert record["discrepancies"] == 0
    assert alert_calls.calls == []

    baseline = shadow_store.read_weights_log(directory=tmp_path)
    assert baseline.equals(weights.sort(["date", "symbol"]))
    assert (tmp_path / "equity.parquet").exists()


def test_a_regression_on_a_past_date_is_detected_alerted_and_baseline_still_advances(
    tmp_path: Path,
):
    days = [D(2026, 1, 1) + timedelta(days=i) for i in range(6)]
    extra_day = days[-1] + timedelta(days=1)
    panel = _panel(days + [extra_day])
    returns = pl.DataFrame(
        {"date": days + [extra_day], "ret": [0.0] + [0.001] * len(days)}
    )

    weights_v1 = _weights(days, 0.6, 0.4)
    shadow_run.run_once(
        today=days[-1],
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: ("ok", panel, ""),
        signal_fn=lambda p: weights_v1,
        index_returns_fn=lambda day: returns,
        alert_fn=Recorder(),
    )

    # Simulate a code change that alters an already-decided date (day[0])
    # while extending the tail by one session -- exactly the failure class
    # this check exists to catch (silent regression vs. deliberate fix).
    weights_v2 = _weights(days[:1], 0.7, 0.3)  # day[0] changed: 0.6/0.4 -> 0.7/0.3
    weights_v2 = pl.concat([weights_v2, _weights(days[1:] + [extra_day], 0.6, 0.4)])
    alert_calls = Recorder()

    record = shadow_run.run_once(
        today=extra_day,
        shadow_dir=tmp_path,
        repo=tmp_path,
        sync_fn=lambda day: ("ok", panel, ""),
        signal_fn=lambda p: weights_v2,
        index_returns_fn=lambda day: returns,
        alert_fn=alert_calls,
    )

    assert record["status"] == "ok"
    assert record["discrepancies"] == 2  # AAA and BBB both changed on day[0]
    assert len(alert_calls.calls) == 1
    assert "discrepancy" in alert_calls.calls[0][0]

    # Baseline advances to tonight's weights regardless (design spec, step 6).
    baseline = shadow_store.read_weights_log(directory=tmp_path)
    assert baseline.equals(weights_v2.sort(["date", "symbol"]))

    discrepancy_log = (tmp_path / "discrepancies.jsonl").read_text().strip().splitlines()
    assert len(discrepancy_log) == 2
    first = json.loads(discrepancy_log[0])
    assert first["symbol"] in ("AAA", "BBB")
    assert first["session"] == str(extra_day)
