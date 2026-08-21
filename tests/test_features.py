"""Tests for the feature library and cross-sectional construction.

The important one is `test_every_feature_is_lookahead_free`: it iterates
`FEATURES`, so a new feature cannot be added without being checked. A library
where one function peeks at the future taints every hypothesis built on it.
"""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from algotrade.backtest import lookahead
from algotrade.core import features as ft
from algotrade.core import strategies as st

D = date


def panel(n_days: int = 400, n_symbols: int = 8) -> pl.DataFrame:
    """Deterministic, non-monotonic panel with every column features may use."""
    start = D(2020, 1, 1)
    rows = []
    for i in range(n_days):
        for j in range(n_symbols):
            close = 100.0 + (i * 7 + j * 13) % 37 + j
            rows.append(
                (
                    start + timedelta(days=i),
                    f"S{j}",
                    close * 0.99,
                    close * 1.02,
                    close * 0.98,
                    close,
                    close * 1.001,
                    1_000_000.0 + (i * 31 + j * 17) % 500_000,
                    40.0 + (i * 3 + j * 11) % 40,
                )
            )
    return pl.DataFrame(
        rows,
        schema={
            "date": pl.Date,
            "symbol": pl.String,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "vwap": pl.Float64,
            "turnover": pl.Float64,
            "deliv_pct": pl.Float64,
        },
        orient="row",
    )


# --- the load-bearing test ---------------------------------------------------


@pytest.mark.parametrize("kind", sorted(ft.FEATURES))
def test_every_feature_is_lookahead_free(kind: str):
    """Iterates the registry, so a new feature is checked automatically."""
    lookahead.assert_no_lookahead(
        panel(), lambda p: ft.add(p, kind), columns=["feature"]
    )


def test_registry_covers_every_public_feature():
    """A feature defined but unregistered would escape the check above."""
    defined = {
        name
        for name in dir(ft)
        if not name.startswith("_")
        and callable(getattr(ft, name))
        and getattr(ft, name).__module__ == ft.__name__
        and name not in {"add"}
    }
    assert defined == set(ft.FEATURES)


# --- individual features -----------------------------------------------------


def test_momentum_skips_the_recent_window():
    """The skip is load-bearing: short-horizon reversal has the opposite sign,
    so including the last month measures a blend of two opposing effects."""
    with_skip = ft.add(panel(), "momentum", lookback=60, skip=21)
    without = ft.add(panel(), "momentum", lookback=60, skip=0)
    # Compare the same (date, symbol) rows -- comparing positionally would let a
    # periodic fixture coincide by accident.
    joined = with_skip.select("date", "symbol", "feature").join(
        without.select("date", "symbol", pl.col("feature").alias("no_skip")),
        on=["date", "symbol"],
    ).drop_nulls()
    differ = (joined["feature"] - joined["no_skip"]).abs() > 1e-9
    assert differ.sum() > 0.5 * joined.height, "the skip must change most values"
    # It also delays availability by `skip` sessions per symbol.
    assert with_skip["feature"].null_count() > without["feature"].null_count()


def test_momentum_is_positive_for_a_riser():
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(60)]
    rising = pl.DataFrame(
        {
            "date": days,
            "symbol": ["A"] * 60,
            "close": [100.0 * (1.01**i) for i in range(60)],
        }
    )
    out = ft.add(rising, "momentum", lookback=20, skip=5)
    assert out["feature"].drop_nulls().min() > 0


def test_realised_vol_ranks_a_calm_name_below_a_wild_one():
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(80)]
    rows = [(d, "CALM", 100.0 + (i % 2) * 0.1) for i, d in enumerate(days)]
    rows += [(d, "WILD", 100.0 + (i % 2) * 20.0) for i, d in enumerate(days)]
    frame = pl.DataFrame(
        rows, schema={"date": pl.Date, "symbol": pl.String, "close": pl.Float64},
        orient="row",
    )
    out = ft.add(frame, "realised_vol", lookback=20).drop_nulls("feature")
    calm = out.filter(pl.col("symbol") == "CALM")["feature"].mean()
    wild = out.filter(pl.col("symbol") == "WILD")["feature"].mean()
    assert calm < wild


def test_distance_from_high_is_zero_at_a_new_high():
    days = [D(2024, 1, 1) + timedelta(days=i) for i in range(30)]
    frame = pl.DataFrame(
        {"date": days, "symbol": ["A"] * 30, "close": [100.0 + i for i in range(30)]}
    )
    out = ft.add(frame, "distance_from_high", lookback=10)
    assert out["feature"].drop_nulls().max() == pytest.approx(0.0)


def test_unknown_feature_names_the_alternatives():
    """A typo in a config file must not fail silently."""
    with pytest.raises(KeyError, match="available"):
        ft.add(panel(), "momentumm")


# --- cross-sectional construction -------------------------------------------


def scored(n_symbols: int = 80) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "date": [D(2024, 1, 1)] * n_symbols,
            "symbol": [f"S{i:03d}" for i in range(n_symbols)],
            "feature": [float(i) for i in range(n_symbols)],
        }
    )


def test_book_is_market_neutral_by_construction():
    w = st.cross_sectional(scored(), n_side=10, gross=1.0)
    assert w["weight"].sum() == pytest.approx(0.0)
    assert w["weight"].abs().sum() == pytest.approx(1.0)
    assert w.height == 20


def test_long_side_direction_is_explicit_not_inferred():
    """Getting this backwards inverts the strategy, so it is a parameter."""
    low = st.cross_sectional(scored(), n_side=5, long_side="low")
    high = st.cross_sectional(scored(), n_side=5, long_side="high")
    longs_low = set(low.filter(pl.col("weight") > 0)["symbol"])
    longs_high = set(high.filter(pl.col("weight") > 0)["symbol"])
    assert longs_low.isdisjoint(longs_high)
    assert "S000" in longs_low  # lowest feature value
    assert "S079" in longs_high  # highest


def test_thin_cross_sections_are_skipped():
    """Top 15 of 20 is not a tail -- the legs would overlap in character."""
    assert st.cross_sectional(scored(n_symbols=20), n_side=15).is_empty()


def test_nulls_are_excluded_from_ranking():
    frame = scored(40).with_columns(
        pl.when(pl.col("symbol") == "S000").then(None).otherwise(pl.col("feature"))
        .alias("feature")
    )
    w = st.cross_sectional(frame, n_side=5)
    assert "S000" not in set(w["symbol"])


def test_long_only_has_no_short_leg():
    w = st.long_only(scored(), n=10, side="high")
    assert (w["weight"] > 0).all()
    assert w["weight"].sum() == pytest.approx(1.0)
    assert "S079" in set(w["symbol"])


def test_construction_requires_a_feature_column():
    with pytest.raises(ValueError, match="feature"):
        st.cross_sectional(pl.DataFrame({"date": [], "symbol": []}))


def test_universe_restriction_uses_only_past_membership():
    """Carrying a *later* rebalance backwards would be look-ahead through the
    universe rather than the signal -- subtler and just as fatal."""
    p = pl.DataFrame(
        {
            "date": [D(2024, 1, 15), D(2024, 2, 15)] * 2,
            "symbol": ["OLD", "OLD", "NEW", "NEW"],
            "feature": [1.0, 1.0, 2.0, 2.0],
        }
    ).sort(["date", "symbol"])
    members = pl.DataFrame(
        {"date": [D(2024, 1, 1), D(2024, 2, 1)], "symbol": ["OLD", "NEW"]}
    )
    out = st.restrict_to_universe(p, members)
    jan = set(out.filter(pl.col("date") == D(2024, 1, 15))["symbol"])
    feb = set(out.filter(pl.col("date") == D(2024, 2, 15))["symbol"])
    assert jan == {"OLD"}  # NEW joins only in February
    assert feb == {"NEW"}
