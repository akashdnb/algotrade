"""Signal parity: the production service must reproduce the research pipeline.

PROJECT.md §9 calls this "the highest-value check in the system" -- a live
signal that silently differs from the backtested one invalidates every number
the deployment decision rests on, while the backtest continues to look fine.

The check is only meaningful against the real archive, so it skips when the
data is absent (CI runners, a fresh clone). It is the manual gate to re-run
after any change to `universe`, `features`, `strategies`, or the panel loader.

    python -m pytest tests/integration/test_signal_parity.py -q
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CURATED = REPO / "data" / "curated"
ACTIONS = REPO / "data" / "raw" / "actions"

pytestmark = pytest.mark.skipif(
    not (CURATED.exists() and ACTIONS.exists()),
    reason="requires the local market-data archive",
)


def _research_top_30(as_of: date) -> list[str]:
    """The ranking exactly as the backtest builds it, on the FULL archive."""
    import importlib
    import sys

    import polars as pl

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    rh = importlib.import_module("scripts.run_hypothesis")
    from algotrade.core import features, strategies, universe

    panel = rh.load_adjusted_panel()
    liquid = universe.liquidity(panel)
    members = universe.membership(liquid, exclude=set(universe.DEFAULT_EXCLUSIONS))
    eligible = strategies.restrict_to_universe(
        features.add(liquid, "momentum", lookback=252, skip=21), members
    )
    return (
        eligible.filter((pl.col("date") == as_of) & pl.col("feature").is_not_null())
        .sort("feature", descending=True)
        .head(30)["symbol"]
        .to_list()
    )


def _production_top_30(as_of: date) -> list[str]:
    """The ranking as the live job computes it, on a ~320-session window."""
    from algotrade.services import panel as panel_service
    from algotrade.services.signals import SignalService, StrategySpec

    spec = StrategySpec.from_yaml(REPO / "config" / "strategy" / "momentum_v1.yaml")
    loaded = panel_service.load(
        curated_dir=CURATED, actions_dir=ACTIONS, through=as_of
    )
    signals = SignalService(spec).generate(loaded.panel, as_of=as_of)
    return [row.symbol for row in signals.top(30)]


def _latest_session() -> date:
    from algotrade.io import store

    return store.read(CURATED, columns=["date"])["date"].max()


def test_production_signal_matches_the_research_pipeline_exactly():
    """Same names, same order.

    The two paths differ in how much history they load -- 16 years versus ~320
    sessions -- and must still agree, because momentum only reaches back
    252 + 21 sessions and the universe window is 20. If this ever fails, the
    live book has diverged from the validated strategy and no live number can
    be trusted until it is explained.
    """
    as_of = _latest_session()
    research = _research_top_30(as_of)
    production = _production_top_30(as_of)

    assert len(research) == 30, "the research path produced no ranking"
    assert production == research, (
        f"signal divergence on {as_of}\n"
        f"  only in research  : {[s for s in research if s not in production]}\n"
        f"  only in production: {[s for s in production if s not in research]}"
    )


def test_the_stored_ranking_runs_deeper_than_the_book():
    """The buffer needs to know whether a HELD name has fallen past rank 60.
    A top-30 list cannot answer that, so signals store to the exit depth."""
    from algotrade.services import panel as panel_service
    from algotrade.services.signals import SignalService, StrategySpec

    as_of = _latest_session()
    spec = StrategySpec.from_yaml(REPO / "config" / "strategy" / "momentum_v1.yaml")
    loaded = panel_service.load(
        curated_dir=CURATED, actions_dir=ACTIONS, through=as_of
    )
    signals = SignalService(spec).generate(loaded.panel, as_of=as_of)

    assert len(signals.rows) == spec.exit == 60
    assert signals.top(30) == sorted(signals.rows, key=lambda r: r.rank)[:30]
