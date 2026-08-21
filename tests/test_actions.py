"""Tests for corporate-action subject parsing.

Subjects are taken verbatim from NSE's corporate-actions API for Jul-Aug 2022,
and the expected factors are cross-checked against the price discontinuities
the integrity module detected independently for the same events.
"""

from __future__ import annotations

import polars as pl
import pytest

from algotrade.core.actions import (
    bonus_factor,
    classify,
    dividend_amount,
    price_factor,
    split_factor,
)

# (subject, expected factor, implied ratio observed in the panel)
REAL_SPLITS = [
    (
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share",
        10.0,
        9.561,  # TATASTEEL 2022-07-28
    ),
    (
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share",
        5.0,
        5.038,  # RGL 2022-07-19
    ),
    (
        "Face Value Split (Sub-Division) - From Rs 5/- Per Share To Re 1/- Per Share",
        5.0,
        4.763,  # RAMASTEEL 2022-08-25
    ),
]

REAL_BONUSES = [
    ("Bonus 1:1", 2.0, 1.952),  # UNOMINDA 2022-07-07
    ("Bonus 2:1", 3.0, 2.830),  # GMMPFAUDLR 2022-07-11
    ("Bonus 1:3", 1 + 1 / 3, 1.302),  # RECLTD 2022-08-17
    ("Bonus 1:2", 1.5, None),  # XPROINDIA 2022-07-01
]


@pytest.mark.parametrize(("subject", "expected", "_implied"), REAL_SPLITS)
def test_split_factor(subject: str, expected: float, _implied: float):
    assert split_factor(subject) == pytest.approx(expected)


@pytest.mark.parametrize(("subject", "expected", "_implied"), REAL_BONUSES)
def test_bonus_factor(subject: str, expected: float, _implied: float):
    assert bonus_factor(subject) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("subject", "expected", "implied"),
    [*REAL_SPLITS, *((s, e, i) for s, e, i in REAL_BONUSES if i)],
)
def test_parsed_factor_agrees_with_observed_price_move(
    subject: str, expected: float, implied: float
):
    """The parsed factor must land near the independently observed ratio.

    They never match exactly: `prev_close` is unadjusted while `close` is, so
    the implied ratio absorbs the stock's own move on the ex-date. GMMPFAUDLR's
    bonus ex-date moved ~6% by itself (factor 3.0 vs implied 2.830), which sets
    the tolerance -- ex-dates are often volatile.

    15% is deliberately loose, and it is still tight enough to matter: the
    §10.6 Trap 1 failure mode (deriving MGEL's factor from `faceVal`, giving 10
    instead of 5) is a 2x error and would be caught easily. Reconciliation
    confirms the magnitude, not the exact ratio -- which is precisely why the
    feed stays authoritative for the factor itself.
    """
    assert price_factor(subject) == pytest.approx(implied, rel=0.15)


def test_split_and_bonus_compose():
    """A subject announcing both multiplies the two effects."""
    subject = (
        "Face Value Split (Sub-Division) - From Rs 10/- Per Share "
        "To Rs 2/- Per Share and Bonus 1:1"
    )
    assert price_factor(subject) == pytest.approx(10.0)


def test_re_and_rs_are_interchangeable():
    """NSE writes 'Re 1' and 'Rs 1' inconsistently for the same thing."""
    a = "Face Value Split - From Rs 10/- Per Share To Re 1/- Per Share"
    b = "Face Value Split - From Rs 10/- Per Share To Rs 1/- Per Share"
    assert split_factor(a) == split_factor(b) == pytest.approx(10.0)


@pytest.mark.parametrize(
    "subject",
    [
        "Dividend - Rs 18 Per Share",
        "Annual General Meeting",
        "Buy Back",
        "Demerger",
        "",
    ],
)
def test_non_price_actions_have_no_factor(subject: str):
    """Demerger genuinely moves the price but by no ratio the subject states.

    Left null on purpose so reconciliation surfaces it as an unexplained
    discontinuity rather than applying a fabricated factor.
    """
    assert price_factor(subject) is None


def test_dividend_amount():
    assert dividend_amount("Dividend - Rs 18 Per Share") == pytest.approx(18.0)
    assert dividend_amount(
        "Annual General Meeting/Dividend - Rs 3.20 Per Share"
    ) == pytest.approx(3.20)
    assert dividend_amount("Bonus 1:1") is None


def test_face_value_must_not_be_used_as_the_factor():
    """§10.6 Trap 1, as a regression test.

    MGEL's real row: faceVal says 1, the subject says the split went to Rs 2.
    The stock split again later, so faceVal reflects today. Only the subject is
    correct for this event.
    """
    row = {
        "symbol": "MGEL",
        "faceVal": 1,
        "subject": (
            "Face Value Split (Sub-Division) - From Rs 10/- Per Share "
            "To Rs 2/- Per Share"
        ),
    }
    assert split_factor(row["subject"]) == pytest.approx(5.0)
    # The faceVal-derived answer would have been 10/1 = 10. Wrong by 2x.
    assert 10.0 / row["faceVal"] != pytest.approx(5.0)


def test_classify_adds_columns():
    frame = pl.DataFrame(
        {
            "symbol": ["TATASTEEL", "RECLTD", "THANGAMAYL", "TRIVENI"],
            "subject": [
                "Face Value Split (Sub-Division) - From Rs 10/- Per Share "
                "To Re 1/- Per Share",
                "Bonus 1:3",
                "Dividend - Rs 18 Per Share",
                "Demerger",
            ],
        }
    )
    out = classify(frame)
    assert out["price_factor"].to_list()[:2] == pytest.approx([10.0, 1 + 1 / 3])
    assert out["price_factor"][2] is None
    assert out["price_factor"][3] is None
    assert out["dividend"][2] == pytest.approx(18.0)


def test_classify_requires_subject_column():
    with pytest.raises(ValueError, match="subject"):
        classify(pl.DataFrame({"symbol": ["X"]}))
