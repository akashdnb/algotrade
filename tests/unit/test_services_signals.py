"""Tests for momentum signal generation.

Composes the same `universe`/`features`/`strategies` functions the backtest
uses, so most correctness lives in those (already tested). What is tested here
is the composition: depth, ordering, failure modes, and the change comparison
that feeds the daily message.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from algotrade.domain.models import SignalRow, SignalSet
from algotrade.services.signals import (
    SignalService,
    SignalUnavailable,
    StrategySpec,
    compare,
)

D = date
NOW = datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)
REPO = Path(__file__).resolve().parents[2]


def _spec(**over) -> StrategySpec:
    kwargs = dict(version="momentum_v1", lookback=20, skip=2, entry=3, exit=6,
                  universe_size=50, universe_lookback=5,
                  min_turnover=1000.0, min_coverage=0.5)
    kwargs.update(over)
    return StrategySpec(**kwargs)


def _panel(symbols: list[str], n_days: int = 60, trend: dict | None = None):
    """Each symbol trends at its own rate, so the ranking is deterministic."""
    trend = trend or {s: 0.001 * (i + 1) for i, s in enumerate(symbols)}
    days = [D(2026, 1, 1) + timedelta(days=i) for i in range(n_days)]
    rows = []
    for symbol in symbols:
        price = 100.0
        for day in days:
            price *= 1 + trend[symbol]
            rows.append({"date": day, "symbol": symbol, "close": price,
                         "turnover": 1e8})
    return pl.DataFrame(rows), days


# ------------------------------------------------------------------ spec ----


def test_the_real_frozen_config_parses():
    spec = StrategySpec.from_yaml(
        REPO / "config" / "strategy" / "momentum_v1.yaml")
    assert (spec.version, spec.lookback, spec.skip) == ("momentum_v1", 252, 21)
    assert (spec.entry, spec.exit) == (30, 60)


def test_the_params_hash_changes_when_any_parameter_changes():
    """The frozen-strategy gate depends on this: an edited config must not
    hash to the same value, or the guard is decorative."""
    base = _spec().params_hash()
    assert _spec(lookback=253).params_hash() != base
    assert _spec(entry=31).params_hash() != base
    assert _spec(min_coverage=0.81).params_hash() != base
    assert _spec().params_hash() == base       # and is stable


# ------------------------------------------------------------ generation ----


def test_names_are_ranked_by_descending_momentum():
    panel, days = _panel(["SLOW", "MID", "FAST"])
    signals = SignalService(_spec()).generate(panel, as_of=days[-1], now=NOW)
    assert [r.symbol for r in signals.rows][:3] == ["FAST", "MID", "SLOW"]
    assert [r.rank for r in signals.rows] == list(range(1, len(signals.rows) + 1))


def test_the_stored_depth_is_the_exit_threshold_not_the_entry():
    """The buffer must know whether a HELD name has fallen past exit. A
    top-`entry` list cannot answer that."""
    panel, days = _panel([f"S{i:02d}" for i in range(10)])
    signals = SignalService(_spec(entry=3, exit=6)).generate(
        panel, as_of=days[-1], now=NOW)
    assert len(signals.rows) == 6


def test_a_close_price_is_attached_to_every_row():
    """The recommendation engine prices trades from this; a missing close
    silently drops the name from the target book."""
    panel, days = _panel(["A", "B", "C"])
    signals = SignalService(_spec()).generate(panel, as_of=days[-1], now=NOW)
    assert all(r.close is not None and r.close > 0 for r in signals.rows)


def test_the_signal_is_keyed_by_strategy_and_date_not_portfolio():
    panel, days = _panel(["A", "B", "C"])
    signals = SignalService(_spec()).generate(panel, as_of=days[-1], now=NOW)
    assert signals.signal_id == f"momentum_v1:{days[-1].isoformat()}"


def test_a_session_absent_from_the_panel_is_refused():
    panel, days = _panel(["A", "B", "C"])
    with pytest.raises(SignalUnavailable, match="not in the panel"):
        SignalService(_spec()).generate(panel, as_of=D(2030, 1, 1), now=NOW)


def test_insufficient_history_produces_a_clear_failure_not_an_empty_book():
    """Momentum is null for every name before lookback+skip sessions. Silently
    returning nothing would look like 'no opportunities' rather than a fault."""
    panel, days = _panel(["A", "B", "C"], n_days=10)
    with pytest.raises(SignalUnavailable, match="insufficient history"):
        SignalService(_spec(lookback=20, skip=2)).generate(
            panel, as_of=days[-1], now=NOW)


def test_the_universe_size_is_recorded():
    panel, days = _panel(["A", "B", "C", "D"])
    signals = SignalService(_spec()).generate(panel, as_of=days[-1], now=NOW)
    assert signals.universe_size == 4


def test_the_panel_fingerprint_is_carried_for_reproducibility():
    panel, days = _panel(["A", "B", "C"])
    signals = SignalService(_spec()).generate(
        panel, as_of=days[-1], panel_manifest_hash="sha256:deadbeef", now=NOW)
    assert signals.panel_manifest_hash == "sha256:deadbeef"


def test_top_and_rank_of_are_consistent():
    panel, days = _panel(["SLOW", "MID", "FAST"])
    signals = SignalService(_spec()).generate(panel, as_of=days[-1], now=NOW)
    assert signals.top(1)[0].symbol == "FAST"
    assert signals.rank_of("FAST") == 1
    assert signals.rank_of("NOTHERE") is None


# ------------------------------------------------------------- comparison ---


def _set(symbols: list[str], as_of=D(2026, 9, 30)) -> SignalSet:
    return SignalSet(strategy_version="momentum_v1", date=as_of,
                     rows=[SignalRow(rank=i + 1, symbol=s, feature=1.0 - i * 0.1)
                           for i, s in enumerate(symbols)])


def test_the_first_signal_reports_no_changes():
    delta = compare(None, _set(["A", "B"]))
    assert delta["first_signal"]
    assert delta["entered"] == [] and delta["exited"] == []


def test_entries_and_exits_are_detected():
    delta = compare(_set(["A", "B", "C"]), _set(["A", "B", "D"]))
    assert delta["entered"] == ["D"]
    assert delta["exited"] == ["C"]


def test_rank_movements_are_reported_biggest_riser_first():
    delta = compare(_set(["A", "B", "C"]), _set(["C", "A", "B"]))
    assert delta["risers"][0][0] == "C"       # rank 3 -> 1
    assert delta["risers"][0][1] == 2
    assert any(s == "B" for s, _ in delta["fallers"])


def test_an_unchanged_ranking_reports_nothing():
    delta = compare(_set(["A", "B", "C"]), _set(["A", "B", "C"]))
    assert delta["entered"] == [] and delta["exited"] == []
    assert delta["risers"] == [] and delta["fallers"] == []
