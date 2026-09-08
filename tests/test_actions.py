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


# --- subject-format variants found in the real feed (145 missed splits) ------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        # The canonical form.
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 10.0),
        # No "Per Share" -- this is what the original regex missed.
        ("Face Value Split From Rs 10 To Re 1", 10.0),
        ("Face Value Split From Rs 10 To Rs 2", 5.0),
        ("Face Value Split From Rs 2 To Re 1", 2.0),
        # Abbreviated.
        ("Fv Splt Frm Rs 10 To Re 1", 10.0),
        ("Fv Splt Frm Rs 5 To Re 1", 5.0),
        # Period, with and without spaces.
        ("Face Value Split From Rs.10/- To Re.1/-", 10.0),
        ("Face Value  Split From Rs. 10 To Rs. 2/-", 5.0),
        # Buried in a compound subject.
        ("Annual General Meeting And Face Value Split From Rs.10/- To Re.1/-", 10.0),
        ("1st Interim Dividend Rs.2.50 Per Share And Face Value Split From Rs.10/- To Re.1/-", 10.0),
        ("Face Value Split From Rs 10 To Re 1 (Record Date Revised)", 10.0),
    ],
)
def test_real_split_subject_variants(subject: str, expected: float):
    assert split_factor(subject) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [("Bonus 1:1", 2.0), ("Bonus - 1:1", 2.0), ("Bonus Issue 1:2", 1.5)],
)
def test_real_bonus_subject_variants(subject: str, expected: float):
    assert bonus_factor(subject) == pytest.approx(expected)


def test_compound_bonus_and_split_multiplies():
    """A real subject announcing both: 1:1 bonus (2x) and a 10->2 split (5x)."""
    subject = "Bonus - 1:1 And Face Value Split From Rs. 10 To Rs. 2"
    assert price_factor(subject) == pytest.approx(10.0)


# --- rights: a ratio, but no factor derivable from the subject ---------------


def test_rights_ratio_is_extracted():
    from algotrade.core.actions import rights_ratio

    assert rights_ratio("Rights 1:10 @ Prem Rs 197/- Per Share") == (1, 10)
    assert rights_ratio("Rights 5: 116 At Premium Rs 244/- Per Share") == (5, 116)
    assert rights_ratio("Bonus 1:1") is None


def test_rights_do_not_produce_a_price_factor():
    """The adjustment needs the market price, which the subject lacks.

    Fabricating a pure-ratio factor for 320 rights rows would corrupt prices,
    so they surface as unexplained discontinuities instead.
    """
    assert price_factor("Rights 1:10 @ Prem Rs 197/- Per Share") is None


# --- splits with no "From" at all (found 2026-09-08) --------------------------
#
# Every subject in the block above contains "From" or "Frm", because the corpus
# was drawn from Jul-Aug 2022 where they all happened to. `_SPLIT_RE` required
# it, so these nine real splits went unadjusted in the 2010-2026 panel -- the
# one the backtest reads. Each is quoted verbatim with the symbol and ex-date
# that carried it.
#
#   SBIN        2014-11-20  closed 2910.50 -> 297.10  (-89.8%)
#   ASIANPAINT  2013-07-30  closed 5118.20 -> 510.75  (-90.0%)
#   M&MFIN      2013-02-15  closed 1009.10 -> 201.80  (-80.0%)
#
# A -90% print stays inside momentum's 252-day lookback for a full year, so one
# missed split misranks a liquid name for four rebalances.

@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        # SBIN 2014-11-20 -- "Per Share" present, "From" absent.
        ("Face Value Split Rs.10/- To Re.1/- Per Share", 10.0),
        # ASIANPAINT 2013-07-30, GRANULES 2015-03-23.
        ("Face Value Split Rs 10 To Re 1", 10.0),
        ("Face Value Split Rs 10 To Rs 1", 10.0),
        # M&MFIN 2013-02-15, SYMPHONY 2012-02-15.
        ("Face Value Split Rs 10 To Rs 2", 5.0),
        ("Face Value Split Rs.10/- To Rs.2/-", 5.0),
        # 20MICRONS 2013-01-28.
        ("Face Value Split Rs 10 To Rs 5", 2.0),
        # TULIP 2010-07-06, PRATIBHA 2010-07-30 -- "Fv" abbreviation.
        ("Fv Split Rs.10/- To Rs.2/-", 5.0),
        # The dominant pre-2010 form, and the trailing "/-" is truncated.
        ("Fv Split Rs.10/- To Rs.5/", 2.0),
        # CORPBANK 2015-01-22. NSE's own typo ("Valus"), and "Per To" rather
        # than "Per Share To" -- so the optional bridge cannot require "share".
        ("Face Valus Split (Sub-Division) - From Rs 10/- Per To Rs 2/- Per Share", 5.0),
    ],
)
def test_a_split_without_the_word_from_is_still_a_split(subject, expected):
    assert split_factor(subject) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        # ASHOKA 2013-07-03: 1:2 bonus (1.5x) and a 10->5 split (2x).
        ("Bonus 1:2 And Face Value Split Rs.10/- To Rs.5/-", 3.0),
        # JBMA 2014-10-08: 1:1 bonus (2x) and a 10->5 split (2x).
        ("Bonus 1:1 And Face Value Split Rs.10/- To Rs.5/- Per Share", 4.0),
    ],
)
def test_a_compound_subject_is_not_adjusted_for_the_bonus_alone(subject, expected):
    """These are worse than a clean miss. The bonus parsed and the split did
    not, so `price_factor` returned a *partial* factor that looks correct --
    2x where 4x was due, with nothing to indicate a half-applied adjustment."""
    assert price_factor(subject) == pytest.approx(expected)


# --- and what must NOT be guessed at ----------------------------------------

def test_a_capital_reduction_is_refused_rather_than_read_as_a_split():
    """MONNETISPA 2018-08-29, verbatim. A reduction to Rs 3.30 followed by a
    consolidation back to Rs 10 is roughly price-neutral, but it matches the
    shape of a split twice over and a naive reading takes only the first leg
    and returns 3.03x.

    Refused, not guessed: §"Fail closed. Never guess, never fall back to a
    string match." A wrong factor is silent -- it produces a plausible price
    series for the wrong corporate event."""
    subject = (" Capital Reduction Rs 10 To Rs 3.30 / "
               "Consolidation Rs 3.30 To Rs.10")
    assert split_factor(subject) is None
    assert price_factor(subject) is None


@pytest.mark.parametrize("subject", [
    "Capital Reduction From Rs 10 To Rs 2",
    "Consolidation Of Shares From Re 1 To Rs 10",
    "Reduction Of Capital Rs.10/- To Rs.1/-",
])
def test_reverse_and_reduction_events_are_refused(subject):
    """A consolidation is a REVERSE split: the factor is below one and the
    share count falls. Feeding it through a split factor adjusts the wrong
    way, which is worse than not adjusting."""
    assert split_factor(subject) is None
