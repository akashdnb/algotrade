"""Feed parsing, corroboration, and the refusals that must stay refusals.

A parsed factor is never trusted on its own: the ex-date prices have to agree
with it before the book is touched. These check both halves, and that the
events which are NOT share-count ratios are declined by name rather than
silently producing a wrong one.
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import polars as pl
import pytest

from algotrade.services import corporate_actions as svc

HELD = {"TDPOWERSYS", "BAJFINANCE", "RELIANCE"}
WINDOW = dict(start=date(2026, 1, 1), end=date(2026, 12, 31))


def row(symbol: str, ex: str, subject: str) -> dict:
    return {"symbol": symbol, "exDate": ex, "subject": subject}


def only(rows: list[dict]):
    found, refused = svc.candidates_from_feed(rows, symbols=HELD, **WINDOW)
    return found, refused


# ------------------------------------------------------------- parsing ------


def test_the_real_tdpowersys_subject_parses_to_two():
    found, _ = only([row(
        "TDPOWERSYS", "24-Aug-2026",
        "Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- "
        "Per Share")])
    assert len(found) == 1
    assert found[0].ratio == Fraction(2)
    assert found[0].kind == "split"


def test_a_bonus_becomes_one_plus_the_ratio():
    found, _ = only([row("RELIANCE", "10-Mar-2026", "Bonus 1:1")])
    assert found[0].ratio == Fraction(2)
    assert found[0].kind == "bonus"

    found, _ = only([row("RELIANCE", "10-Mar-2026", "Bonus 1:3")])
    assert found[0].ratio == Fraction(4, 3)


def test_sibling_rows_on_one_ex_date_multiply():
    """The BAJFINANCE case (§10.6): a 2:1 split and a 4:1 bonus announced as
    two rows on 2025-06-16 compose to 10.0. Keeping only the first left a
    phantom -89.9% session in a top-liquidity name."""
    found, _ = only([
        row("BAJFINANCE", "16-Jun-2026",
            "Face Value Split From Rs 2/- Per Share To Re 1/- Per Share"),
        row("BAJFINANCE", "16-Jun-2026", "Bonus 4:1"),
    ])
    assert len(found) == 1
    assert found[0].ratio == Fraction(10)


def test_symbols_not_held_are_ignored():
    found, refused = only([row("SOMEOTHER", "10-Mar-2026", "Bonus 1:1")])
    assert found == [] and refused == []


def test_a_dividend_is_not_a_share_count_change():
    found, refused = only([row("RELIANCE", "10-Mar-2026",
                               "Dividend - Rs 1.10 Per Share")])
    assert found == [] and refused == []


# ------------------------------------------------- deliberate refusals ------


@pytest.mark.parametrize("subject,why", [
    ("Rights 1:4 @ Premium Rs 100", "rights"),
    ("Scheme of Arrangement - Demerger", "demerger"),
    ("Buyback of Equity Shares", "buyback"),
    (" Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To Rs.10",
     "consolidation"),
])
def test_events_that_are_not_ratios_are_refused_by_name(subject, why):
    """Refused, not guessed at -- and the reason names the actual event so it
    does not read as a parser failure."""
    found, refused = only([row("RELIANCE", "10-Mar-2026", subject)])
    assert found == []
    assert len(refused) == 1
    assert why in refused[0][1]


# -------------------------------------------------------- corroboration -----


def panel_with(symbol: str, on: date, prev_close: float, open_: float):
    return pl.DataFrame({
        "symbol": [symbol], "date": [on],
        "prev_close": [prev_close], "open": [open_],
    })


def candidate(ratio=Fraction(2), symbol="TDPOWERSYS", on=date(2026, 8, 24)):
    return svc.Candidate(symbol, on, ratio, "split", "subject")


def test_a_real_split_is_corroborated_by_the_ex_date_prices():
    """TDPOWERSYS actually printed 1,534.80 -> 775.00, an implied 1.98."""
    out = svc.corroborate(
        [candidate()],
        panel_with("TDPOWERSYS", date(2026, 8, 24), 1534.80, 775.00))
    assert out[0].corroborated
    assert out[0].implied_ratio == pytest.approx(1.98, abs=0.01)


def test_a_factor_the_prices_do_not_support_is_not_corroborated():
    """The mis-parse guard: claiming 10x where the price moved 2x."""
    out = svc.corroborate(
        [candidate(ratio=Fraction(10))],
        panel_with("TDPOWERSYS", date(2026, 8, 24), 1534.80, 775.00))
    assert not out[0].corroborated


def test_a_missing_session_is_not_corroboration():
    """Absence of evidence is not evidence -- it must not pass."""
    out = svc.corroborate([candidate()], pl.DataFrame())
    assert not out[0].corroborated
    assert out[0].deviation is None

    out = svc.corroborate(
        [candidate()],
        panel_with("SOMETHINGELSE", date(2026, 8, 24), 100.0, 50.0))
    assert not out[0].corroborated


def test_a_ten_to_one_split_still_corroborates_despite_a_loose_implied_ratio():
    """TATASTEEL's 1:10 implied 9.56, not 10, because the stock also moved.
    A tight tolerance here rejects real actions (§10.6)."""
    out = svc.corroborate(
        [candidate(ratio=Fraction(10))],
        panel_with("TDPOWERSYS", date(2026, 8, 24), 956.0, 100.0))
    assert out[0].corroborated
