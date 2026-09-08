"""Parse NSE corporate-action subjects into price-adjustment factors.

NSE's `subject` field is structured enough to parse reliably:

    "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"
    "Bonus 1:1"
    "Dividend - Rs 18 Per Share"

**The factor must come from the subject, never from `faceVal`** (PROJECT.md §10.6
Trap 1). `faceVal` carries the share's *current* face value, not its value after
the action in question, so any stock that split more than once yields a silently
wrong factor. Observed: MGEL's 2022-08-18 row reads `faceVal = 1` while its
subject says "From Rs 10/- To Rs 2/-", because it split again later.

Bonus ratios follow (new : held), so "Bonus a:b" multiplies the share count by
`1 + a/b`. Verified against detected price discontinuities:

    Bonus 1:1  -> 2.000   (UNOMINDA, implied 1.952)
    Bonus 2:1  -> 3.000   (GMMPFAUDLR, implied 2.830)
    Bonus 1:3  -> 1.333   (RECLTD, implied 1.302)
    Split 10:1 -> 10.000  (TATASTEEL, implied 9.561)

Pure module: strings and DataFrames in, DataFrames out. No I/O.
"""

from __future__ import annotations

import re
from typing import Final

import polars as pl

# Split subjects vary far more than the canonical form suggests. All of these
# occur in the real feed, and an earlier regex that required "Per Share"
# between the two values silently missed 145 splits:
#
#   "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share"
#   "Face Value Split From Rs 10 To Re 1"                   <- no "Per Share"
#   "Fv Splt Frm Rs 10 To Re 1"                             <- abbreviated
#   "Face Value Split From Rs.10/- To Re.1/-"               <- period, no space
#   "Face Value  Split From Rs. 10 To Rs. 2/-"              <- period and space
#   "Bonus - 1:1 And Face Value Split From Rs. 10 To Rs. 2" <- both at once
#
# "Rs" and "Re" are interchangeable and the "/-" suffix is optional throughout.
#
# **"From" is optional.** Requiring it left nine real splits unadjusted in the
# 2010-2026 panel, including SBIN 10:1 (2014-11-20) and ASIANPAINT 10:1
# (2013-07-30), which the panel therefore recorded as -89.8% and -90.0% single
# sessions. Every subject in the original corpus happened to contain it because
# that corpus was one two-month window:
#
#   "Face Value Split Rs.10/- To Re.1/- Per Share"     <- no "From"  (SBIN)
#   "Face Value Split Rs 10 To Re 1"                   <- no "From"  (ASIANPAINT)
#   "Fv Split Rs.10/- To Rs.5/"                        <- the pre-2010 form
#   "... - From Rs 10/- Per To Rs 2/- Per Share"       <- "Per To"   (CORPBANK)
#
# The bridge between the two values is therefore anything short, not
# specifically "per share".
_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:from|frm)?\s*r[se]\.?\s*(\d+(?:\.\d+)?)\s*/?-?\s*"
    r"(?:per(?:\s+share)?\s+)?to\s+r[se]\.?\s*(\d+(?:\.\d+)?)",
    re.I,
)
# Events shaped exactly like a split that are NOT one, and must be refused
# rather than guessed at. MONNETISPA 2018-08-29 is the case that found this:
#
#   " Capital Reduction Rs 10 To Rs 3.30 / Consolidation Rs 3.30 To Rs.10"
#
# Two split-shaped clauses whose net price effect is roughly nil. Reading the
# first leg alone yields 3.03x. A consolidation is a *reverse* split -- the
# share count falls -- so treating it as a split adjusts the wrong direction,
# which is worse than not adjusting at all.
_NOT_A_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"capital\s+reduction|reduction\s+of\s+capital|consolidat", re.I
)
# "Bonus 1:1", "Bonus - 1:1", "Bonus Issue 1:2".
_BONUS_RE: Final[re.Pattern[str]] = re.compile(
    r"bonus[\s\-]*(?:issue)?[\s\-]*(\d+)\s*:\s*(\d+)", re.I
)
# Rights carry a ratio, but the price adjustment also depends on the
# subscription price relative to the market price, so no factor is derivable
# from the subject alone. Extracted separately and deliberately kept out of
# `price_factor` -- see `rights_ratio`.
_RIGHTS_RE: Final[re.Pattern[str]] = re.compile(r"rights\s*(\d+)\s*:\s*(\d+)", re.I)
_DIVIDEND_RE: Final[re.Pattern[str]] = re.compile(
    r"dividend[^\d]*r[se]\.?\s*(\d+(?:\.\d+)?)", re.I
)


def split_factor(subject: str) -> float | None:
    """Price-adjustment factor for a face-value split, or None.

    A split from Rs 10 to Rs 2 multiplies the share count fivefold, so
    historical prices divide by 5.
    """
    if not subject or _NOT_A_SPLIT_RE.search(subject):
        return None
    if (m := _SPLIT_RE.search(subject)) is None:
        return None
    old, new = float(m.group(1)), float(m.group(2))
    if new <= 0 or old <= 0:
        return None
    return old / new


def bonus_factor(subject: str) -> float | None:
    """Price-adjustment factor for a bonus issue, or None.

    "Bonus a:b" means a new shares per b held, so the count scales by 1 + a/b.
    """
    if not subject or (m := _BONUS_RE.search(subject)) is None:
        return None
    new, held = int(m.group(1)), int(m.group(2))
    if held <= 0:
        return None
    return 1.0 + new / held


def dividend_amount(subject: str) -> float | None:
    """Rupees per share for a dividend, or None.

    Worth adjusting for: unadjusted dividends depress measured returns and bias
    a cross-sectional ranking against high-yield names (§10.6).
    """
    if not subject or (m := _DIVIDEND_RE.search(subject)) is None:
        return None
    return float(m.group(1))


def rights_ratio(subject: str) -> tuple[int, int] | None:
    """The (new, held) ratio of a rights issue, or None.

    Deliberately *not* folded into `price_factor`. A rights issue adjusts the
    price toward the theoretical ex-rights value, which depends on the
    subscription price relative to the market price:

        factor = ((held x P_cum) + (new x S)) / ((held + new) x P_cum)

    `S` is in the subject but `P_cum` is not, so a correct factor needs panel
    context. Rights therefore surface as unexplained discontinuities until
    handled explicitly, which is the honest outcome -- 293 rights rows exist in
    the feed and fabricating a pure-ratio factor for them would corrupt prices.
    """
    if not subject or (m := _RIGHTS_RE.search(subject)) is None:
        return None
    new, held = int(m.group(1)), int(m.group(2))
    return (new, held) if held > 0 else None


def price_factor(subject: str) -> float | None:
    """Combined split/bonus factor, or None if the subject implies neither.

    Splits and bonuses are checked independently because a single subject can
    announce both; their effects multiply.
    """
    parts = [f for f in (split_factor(subject), bonus_factor(subject)) if f]
    if not parts:
        return None
    factor = 1.0
    for part in parts:
        factor *= part
    return factor


def classify(actions: pl.DataFrame, subject_col: str = "subject") -> pl.DataFrame:
    """Add `price_factor` and `dividend` columns to a corporate-action frame.

    Rows implying no price adjustment get a null `price_factor`; that includes
    dividends, AGMs, buybacks and demergers. Demergers do move the price but
    by no ratio expressible from the subject, so they are deliberately left for
    the reconciliation step to surface as unexplained discontinuities.
    """
    if subject_col not in actions.columns:
        raise ValueError(f"missing column {subject_col!r}")
    subjects = actions[subject_col].fill_null("").to_list()
    return actions.with_columns(
        pl.Series("price_factor", [price_factor(s) for s in subjects], pl.Float64),
        pl.Series("dividend", [dividend_amount(s) for s in subjects], pl.Float64),
    )
