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

# "From Rs 10/- Per Share To Re 1/- Per Share". Rs and Re are used
# interchangeably, and the "/-" suffix is inconsistent.
_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"from\s+r[se]\.?\s*(\d+(?:\.\d+)?)\s*/?-?\s*per\s+share\s+"
    r"to\s+r[se]\.?\s*(\d+(?:\.\d+)?)",
    re.I,
)
_BONUS_RE: Final[re.Pattern[str]] = re.compile(
    r"bonus\s*(?:issue)?\s*(\d+)\s*:\s*(\d+)", re.I
)
_DIVIDEND_RE: Final[re.Pattern[str]] = re.compile(
    r"dividend[^\d]*r[se]\.?\s*(\d+(?:\.\d+)?)", re.I
)


def split_factor(subject: str) -> float | None:
    """Price-adjustment factor for a face-value split, or None.

    A split from Rs 10 to Rs 2 multiplies the share count fivefold, so
    historical prices divide by 5.
    """
    if not subject or (m := _SPLIT_RE.search(subject)) is None:
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
