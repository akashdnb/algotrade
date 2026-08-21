"""Tests for feed-vs-price reconciliation (PROJECT.md §10.6)."""

from __future__ import annotations

from datetime import date

import polars as pl
import pytest

from algotrade.core import reconcile as rc

D = date


def moves(rows: list[tuple[date, str, float]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={"date": pl.Date, "symbol": pl.String, "implied_ratio": pl.Float64},
        orient="row",
    )


def feed(rows: list[tuple[str, date, float, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows,
        schema={
            "symbol": pl.String,
            "ex_date": pl.Date,
            "price_factor": pl.Float64,
            "subject": pl.String,
        },
        orient="row",
    )


def renames(rows: list[tuple[str, str]]) -> pl.DataFrame:
    return pl.DataFrame(
        rows, schema={"old_symbol": pl.String, "new_symbol": pl.String}, orient="row"
    )


# --- rename chain resolution -------------------------------------------------


def test_chain_follows_multiple_renames():
    """A stock renamed twice must resolve to its latest name."""
    chains = rc.resolve_chains(renames([("A", "B"), ("B", "C")]))
    got = dict(
        zip(chains["historical_symbol"].to_list(), chains["current_symbol"].to_list())
    )
    assert got["A"] == "C"
    assert got["B"] == "C"


def test_chain_terminates_on_a_cycle():
    """Invalid data must not hang the resolver."""
    chains = rc.resolve_chains(renames([("A", "B"), ("B", "A")]))
    assert chains.height == 2


def test_empty_renames_is_handled():
    assert rc.resolve_chains(renames([])).is_empty()


def test_to_current_passes_through_unrenamed():
    chains = rc.resolve_chains(renames([("OLD", "NEW")]))
    out = rc.to_current(moves([(D(2022, 1, 1), "UNTOUCHED", 2.0)]), chains)
    assert out["current_symbol"][0] == "UNTOUCHED"


# --- reconciliation ----------------------------------------------------------


def test_rename_bridge_lets_a_renamed_stock_match():
    """§10.6 Trap 2, as a regression test.

    The real case: the feed reports UNOMINDA's 2022-07-07 bonus, but the panel
    holds the move under MINDAIND. Joining on symbol drops it silently; the
    rename chain is what makes the match possible.
    """
    result = rc.reconcile(
        moves([(D(2022, 7, 7), "MINDAIND", 1.951868)]),
        feed([("UNOMINDA", D(2022, 7, 7), 2.0, "Bonus 1:1")]),
        renames([("MINDAIND", "UNOMINDA")]),
    )
    assert result["matched"].height == 1
    assert result["move_no_action"].is_empty()
    assert result["matched"]["deviation"][0] == pytest.approx(0.024, abs=0.001)


def test_without_the_bridge_the_action_is_lost():
    """Demonstrates the failure the bridge prevents."""
    result = rc.reconcile(
        moves([(D(2022, 7, 7), "MINDAIND", 1.951868)]),
        feed([("UNOMINDA", D(2022, 7, 7), 2.0, "Bonus 1:1")]),
        renames([]),
    )
    assert result["matched"].is_empty()
    assert result["move_no_action"].height == 1
    assert result["action_no_move"].height == 1


def test_factor_mismatch_is_flagged():
    """A 2x error -- what deriving the factor from faceVal would produce."""
    result = rc.reconcile(
        moves([(D(2022, 8, 18), "MGEL", 4.55)]),
        feed([("MGEL", D(2022, 8, 18), 10.0, "Face Value Split")]),
        renames([]),
    )
    assert result["matched"].is_empty()
    assert result["factor_mismatch"].height == 1


def test_volatile_ex_date_still_matches():
    """GMMPFAUDLR's bonus ex-date moved ~6% on its own and must not alarm."""
    result = rc.reconcile(
        moves([(D(2022, 7, 11), "GMMPFAUDLR", 2.830481)]),
        feed([("GMMPFAUDLR", D(2022, 7, 11), 3.0, "Bonus 2:1")]),
        renames([]),
    )
    assert result["matched"].height == 1


def test_move_with_no_action_is_surfaced():
    """The dangerous bucket: a phantom crash left in history by a missed action.

    A cross-sectional strategy ranks on extremes, so an unadjusted 50% drop
    lands at the very top of a "buy the losers" screen.
    """
    result = rc.reconcile(
        moves([(D(2022, 8, 30), "PEL", 1.811)]),
        feed([]),
        renames([]),
    )
    assert result["move_no_action"].height == 1
    assert result["move_no_action"]["symbol"][0] == "PEL"


def test_rows_without_a_price_factor_are_ignored():
    """Dividends, AGMs and buybacks imply no price adjustment.

    Demergers do move the price, but by no ratio derivable from the subject, so
    they are left to surface as move_no_action for manual handling.
    """
    result = rc.reconcile(
        moves([]),
        feed(
            [
                ("FOO", D(2022, 1, 1), None, "Dividend - Rs 5 Per Share"),
                ("BAR", D(2022, 1, 2), None, "Demerger"),
            ]
        ),
        renames([]),
    )
    assert all(frame.is_empty() for frame in result.values())


def test_summarise_counts_every_outcome():
    result = rc.reconcile(
        moves([(D(2022, 7, 7), "A", 2.0), (D(2022, 7, 8), "ORPHAN", 3.0)]),
        feed(
            [
                ("A", D(2022, 7, 7), 2.0, "Bonus 1:1"),
                ("GHOST", D(2022, 7, 9), 5.0, "Split"),
            ]
        ),
        renames([]),
    )
    counts = {r["outcome"]: r["count"] for r in rc.summarise(result).to_dicts()}
    assert counts == {
        "matched": 1,
        "factor_mismatch": 0,
        "action_no_move": 1,
        "move_no_action": 1,
    }
