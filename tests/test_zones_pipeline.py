from datetime import date, timedelta

import polars as pl

from algotrade.zones.config import DemandSupplyConfig, Timeframe
from algotrade.zones.pipeline import _eligible_symbols


def _panel(n=25):
    start = date(2024, 1, 1)
    rows = []
    for i in range(n):
        day = start + timedelta(days=i)
        # Liquid name: steady, well-traded turnover, comfortably above the
        # Rs 5 crore/day default floor.
        rows.append({"symbol": "LIQUIDCO", "date": day, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1_000_000.0, "turnover": 1.0e8})
        # Thin name: trades, but turnover is far below the floor -- the
        # pattern behind the near-zero-denominator volume_ratio outliers.
        rows.append({"symbol": "THINCO", "date": day, "open": 50.0, "high": 50.5, "low": 49.5, "close": 50.0, "volume": 200.0, "turnover": 10_000.0})
    return pl.DataFrame(rows)


def test_liquidity_floor_excludes_thin_names_by_default():
    panel = _panel()
    as_of = panel["date"].max()
    config = DemandSupplyConfig(Timeframe.DAILY)  # default min_turnover=5e7, min_coverage=0.80
    symbols = _eligible_symbols(panel, as_of, min_turnover=config.min_turnover, min_coverage=config.min_coverage)
    assert symbols == ["LIQUIDCO"]


def test_liquidity_floor_is_opt_out_at_zero():
    panel = _panel()
    as_of = panel["date"].max()
    symbols = _eligible_symbols(panel, as_of, min_turnover=0.0, min_coverage=0.0)
    assert symbols == ["LIQUIDCO", "THINCO"]
