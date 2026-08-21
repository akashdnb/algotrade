"""Tests for corporate-action price adjustment (PROJECT.md §10.6)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from algotrade.core import adjust as adj

D = date

COLS = {
    "date": pl.Date,
    "symbol": pl.String,
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "prev_close": pl.Float64,
    "vwap": pl.Float64,
    "volume": pl.Int64,
    "turnover": pl.Float64,
    "deliv_qty": pl.Int64,
    "deliv_pct": pl.Float64,
}


def panel(rows: list[tuple]) -> pl.DataFrame:
    """(date, symbol, close, prev_close, volume, turnover) -> full panel."""
    built = [
        (d, s, c, c, c, c, pc, c, v, t, v // 2, 50.0)
        for d, s, c, pc, v, t in rows
    ]
    return pl.DataFrame(built, schema=COLS, orient="row")


def factors(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"symbol": pl.String, "ex_date": pl.Date, "factor": pl.Float64},
        orient="row",
    )


# --- the real TATASTEEL case --------------------------------------------------


def tatasteel() -> pl.DataFrame:
    """Real closes around the 1:10 split with ex-date 2022-07-28."""
    return panel(
        [
            (D(2022, 7, 26), "TATASTEEL", 949.50, 96.07 * 10, 12_626_469, 1.2e10),
            (D(2022, 7, 27), "TATASTEEL", 959.40, 949.50, 5_255_902, 5.0e9),
            (D(2022, 7, 28), "TATASTEEL", 100.35, 959.40, 137_156_107, 1.36e10),
            (D(2022, 7, 29), "TATASTEEL", 107.60, 100.35, 166_959_934, 1.78e10),
        ]
    )


def test_prices_before_ex_date_are_divided():
    out = adj.adjust(tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), 10.0)]))
    closes = out.sort("date")["close"].to_list()
    assert closes == pytest.approx([94.95, 95.94, 100.35, 107.60])


def test_ex_date_row_excludes_its_own_factor():
    """The market price on the ex-date is already post-split."""
    out = adj.adjust(
        tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), 10.0)]), keep_raw=True
    )
    row = out.filter(pl.col("date") == D(2022, 7, 28)).row(0, named=True)
    assert row["divisor"] == pytest.approx(1.0)
    assert row["close"] == pytest.approx(row["close_raw"])


def test_series_becomes_continuous():
    """The point of the exercise: no jump remains across the ex-date."""
    out = adj.adjust(tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), 10.0)]))
    closes = out.sort("date")["close"].to_list()
    rets = [b / a - 1 for a, b in zip(closes, closes[1:])]
    assert max(abs(r) for r in rets) < 0.10


def test_prev_close_is_recomputed_not_scaled():
    """NSE leaves prev_close unadjusted on the ex-date -- that is the very
    discontinuity the integrity checks use. Scaling it by the row's own divisor
    would leave it wrong by exactly the factor, so it is recomputed."""
    out = adj.adjust(tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), 10.0)]))
    ex = out.filter(pl.col("date") == D(2022, 7, 28)).row(0, named=True)
    assert ex["prev_close"] == pytest.approx(95.94)  # previous *adjusted* close
    assert ex["prev_close"] != pytest.approx(959.40)  # the raw value
    assert ex["prev_close"] != pytest.approx(95.94 * 10)  # naive scaling


# --- field asymmetry ---------------------------------------------------------


def test_quantities_multiply_and_turnover_is_invariant():
    """Prices divide, share counts multiply, rupee turnover does not move."""
    raw = tatasteel()
    out = adj.adjust(raw, factors([("TATASTEEL", D(2022, 7, 28), 10.0)]))
    first_raw = raw.sort("date").row(0, named=True)
    first_adj = out.sort("date").row(0, named=True)
    assert first_adj["volume"] == pytest.approx(first_raw["volume"] * 10)
    assert first_adj["deliv_qty"] == pytest.approx(first_raw["deliv_qty"] * 10)
    assert first_adj["turnover"] == pytest.approx(first_raw["turnover"])
    assert first_adj["deliv_pct"] == pytest.approx(first_raw["deliv_pct"])


def test_all_price_columns_are_scaled():
    out = adj.adjust(tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), 10.0)]))
    row = out.sort("date").row(0, named=True)
    for col in ("open", "high", "low", "close", "vwap"):
        assert row[col] == pytest.approx(94.95), col


# --- composition and edge cases ---------------------------------------------


def test_successive_actions_compound():
    """Two splits: the earliest prices divide by the product of both."""
    rows = [
        (D(2024, 1, 1), "A", 1000.0, 1000.0, 100, 1e6),
        (D(2024, 2, 1), "A", 100.0, 1000.0, 100, 1e6),  # 1:10
        (D(2024, 3, 1), "A", 50.0, 100.0, 100, 1e6),  # 1:2
    ]
    out = adj.adjust(
        panel(rows),
        factors([("A", D(2024, 2, 1), 10.0), ("A", D(2024, 3, 1), 2.0)]),
        keep_raw=True,
    ).sort("date")
    assert out["divisor"].to_list() == pytest.approx([20.0, 2.0, 1.0])
    assert out["close"].to_list() == pytest.approx([50.0, 50.0, 50.0])


def test_untouched_symbols_are_unchanged():
    rows = [
        (D(2024, 1, 1), "A", 100.0, 100.0, 10, 1e5),
        (D(2024, 1, 1), "B", 200.0, 200.0, 10, 1e5),
        (D(2024, 2, 1), "A", 10.0, 100.0, 10, 1e5),
        (D(2024, 2, 1), "B", 210.0, 200.0, 10, 1e5),
    ]
    out = adj.adjust(panel(rows), factors([("A", D(2024, 2, 1), 10.0)]))
    b = out.filter(pl.col("symbol") == "B").sort("date")
    assert b["close"].to_list() == pytest.approx([200.0, 210.0])


def test_empty_factors_is_a_passthrough():
    raw = tatasteel()
    out = adj.adjust(raw, factors([]))
    assert out.sort("date")["close"].to_list() == pytest.approx(
        raw.sort("date")["close"].to_list()
    )


def test_non_positive_factor_is_rejected():
    """A zero or negative factor would flip or blow up prices."""
    for bad in (0.0, -2.0):
        with pytest.raises(ValueError, match="non-positive"):
            adj.adjust(tatasteel(), factors([("TATASTEEL", D(2022, 7, 28), bad)]))


# --- selecting which factors to trust ---------------------------------------


def test_only_corroborated_actions_are_applied_by_default():
    """Mismatched factors are excluded: the feed and prices disagree there, so
    one is wrong and applying either silently is worse than leaving the gap."""
    result = {
        "matched": pl.DataFrame(
            {
                "date": [D(2022, 7, 28)],
                "symbol": ["TATASTEEL"],
                "price_factor": [10.0],
            }
        ),
        "factor_mismatch": pl.DataFrame(
            {"date": [D(2022, 7, 21)], "symbol": ["HNDFDS"], "price_factor": [5.0]}
        ),
        "action_no_move": pl.DataFrame(),
        "move_no_action": pl.DataFrame(),
    }
    assert adj.factors_from_reconciliation(result)["symbol"].to_list() == ["TATASTEEL"]
    both = adj.factors_from_reconciliation(result, include_mismatched=True)
    assert set(both["symbol"]) == {"TATASTEEL", "HNDFDS"}


def test_unadjusted_symbols_flags_phantom_gaps():
    """Symbols with an unexplained move must leave the universe, not be guessed at."""
    result = {
        "move_no_action": pl.DataFrame(
            {
                "date": [D(2022, 8, 30), D(2022, 8, 31)],
                "symbol": ["PEL", "TINY"],
                "implied_ratio": [1.81, 1.02],  # 1.02 is normal noise
            }
        )
    }
    assert adj.unadjusted_symbols(result) == ["PEL"]


def test_unadjusted_symbols_empty_when_clean():
    assert adj.unadjusted_symbols({"move_no_action": pl.DataFrame()}) == []
