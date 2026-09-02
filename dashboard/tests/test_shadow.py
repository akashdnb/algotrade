"""Tests for pairing a book with its shadow and reading the gap."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app import shadow

PAPER = "paper_momentum_v1_001"
SHADOW = "shadow_momentum_v1_001"
OTHER = "paper_meanrev_v1_001"

OPTIONS = [{"_id": PAPER}, {"_id": SHADOW}, {"_id": OTHER}]


def _series(pairs):
    return [{"date": date(2026, 8, d), "unit_nav": Decimal(str(v))}
            for d, v in pairs]


# ---------------------------------------------------------- counterpart ---

def test_a_book_finds_its_shadow():
    assert shadow.counterpart(PAPER, OPTIONS) == SHADOW


def test_a_shadow_finds_the_book_it_shadows():
    assert shadow.counterpart(SHADOW, OPTIONS) == PAPER


def test_a_different_strategy_is_not_a_counterpart():
    """`paper_meanrev` and `shadow_momentum` measure different things; calling
    the difference between them discretion would be nonsense."""
    assert shadow.counterpart(OTHER, OPTIONS) is None


def test_no_pair_is_the_normal_state():
    assert shadow.counterpart(PAPER, [{"_id": PAPER}]) is None


# ---------------------------------------------------------------- align ---

def test_only_shared_sessions_are_compared():
    """A shadow forked mid-life is shorter. Rebasing the two to different
    first sessions would invent a divergence at the very first point."""
    left, right = shadow.align(
        _series([(3, 10), (4, 11), (5, 12)]), _series([(4, 20), (5, 21)]))
    assert [r["date"].day for r in left] == [4, 5]
    assert [r["date"].day for r in right] == [4, 5]


def test_rows_without_a_unit_nav_are_dropped_from_both_sides():
    left = _series([(4, 11), (5, 12)])
    right = _series([(4, 20), (5, 21)])
    right[0]["unit_nav"] = None
    aligned_left, aligned_right = shadow.align(left, right)
    assert [r["date"].day for r in aligned_left] == [5]
    assert [r["date"].day for r in aligned_right] == [5]


# ----------------------------------------------------------- divergence ---

def test_identical_books_report_zero_but_not_as_a_measurement():
    """Before the first hand-entered fill the two books ARE the same book.
    A gap of zero must not read as 'discretion was shown to cost nothing'."""
    same = _series([(3, 10), (4, 11), (5, 12)])
    result = shadow.divergence(same, _series([(3, 10), (4, 11), (5, 12)]))
    assert result["gap"] == 0.0
    assert result["measured"] is False


def test_a_book_that_lagged_its_shadow_reports_a_negative_gap():
    result = shadow.divergence(
        _series([(3, 100), (4, 102)]), _series([(3, 100), (4, 105)]))
    assert round(result["traded_return"], 10) == 0.02
    assert round(result["shadow_return"], 10) == 0.05
    assert round(result["gap"], 10) == -0.03
    assert result["measured"] is True


def test_the_gap_is_measured_from_the_first_shared_session():
    """Not from inception. A shadow forked today has had no time to differ,
    and dating the comparison to inception would blame discretion for a
    history it was never involved in."""
    traded = _series([(1, 90), (3, 100), (4, 110)])
    result = shadow.divergence(traded, _series([(3, 100), (4, 105)]))
    assert result["from"] == date(2026, 8, 3)
    assert result["sessions"] == 2
    assert round(result["traded_return"], 10) == 0.10


def test_one_shared_session_is_not_a_comparison():
    assert shadow.divergence(_series([(4, 11)]), _series([(4, 11)])) is None


def test_no_overlap_at_all_reports_nothing():
    assert shadow.divergence(_series([(3, 10)]), _series([(9, 10)])) is None
