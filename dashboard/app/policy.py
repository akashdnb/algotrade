"""The Investment Policy's measured expectations, shown next to live figures.

A drawdown reading means nothing on its own. `-2.6%` is obviously nothing when
you can see that `-41.6%` is the expected worst case; the same number alone, on
a red page, in month four, is where people sell -- and §11p priced that
decision at 27-40% of terminal wealth.

So these are not decoration. They are the reason the summary page exists at
all, and they are deliberately **static constants**, not computed: they come
from INVESTMENT-POLICY.md, which is a commitment made before any capital was
at risk. A number that recalculates as results arrive is not a commitment.

Source: `INVESTMENT-POLICY.md` §4, for the 50/50 allocation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Expectation:
    label: str
    value: str
    note: str


EXPECTED_PAIN: tuple[Expectation, ...] = (
    Expectation("Maximum drawdown", "−41.6%",
                "measured over the archive, at the 50/50 allocation"),
    Expectation("Longest drawdown", "1,027 days",
                "2.8 years below the previous peak"),
    Expectation("Longest lag vs NIFTY", "1,198 days",
                "3.3 years of trailing the index is the expected case"),
    Expectation("Worst relative shortfall", "−39.7%",
                "over 39.5 months, starting January 2018"),
)

#: Rule 4 of the policy, quoted rather than paraphrased. It is the one that
#: costs the most to break: selling the book costs 33-40% of terminal wealth
#: whenever it is done, and unlike every other panic response it does not get
#: cheaper by waiting.
RULE = (
    "Existing positions are never liquidated because of drawdown. "
    "Underperformance versus NIFTY is not a reason to sell — not at −10%, "
    "not at −30%, not after three years."
)

POLICY_PATH = "INVESTMENT-POLICY.md"
