"""The Investment Policy's measured expectations, shown next to live figures.

A drawdown reading means nothing on its own. `-2.6%` is obviously nothing when
you can see the expected worst case; the same number alone, on a red page, in
month four, is where people sell -- and §11p priced that decision at 27-40% of
terminal wealth.

So these are not decoration. They are the reason the summary page exists, and
they are deliberately **static constants**, not computed: they come from
INVESTMENT-POLICY.md, a commitment made before any capital was at risk. A
number that recalculates as results arrive is not a commitment.

**Which book each figure describes matters, and getting it wrong is
dangerous.** This dashboard shows the MOMENTUM SLEEVE: `benchmark_sleeve` is
null on the portfolio and `benchmark_sleeve_value` is null on every snapshot,
because the NIFTYBEES half of the 50/50 allocation is held manually, outside
the system. The Investment Policy's headline expected-pain table is for the
**blended 50/50 book** -- a materially gentler thing.

The first version of this panel showed the 50/50 figures beside a
momentum-only NAV: expected max drawdown -41.6% against a book whose real
expectation is **-53.1%**. That understates the pain by 11.5 points, and it
errs in the direction that causes the harm: a -48% drawdown would have looked
like the strategy breaking its own stated limit, when -53.1% was always the
number. Every figure below now carries the allocation it belongs to.

Source: `INVESTMENT-POLICY.md` §1 and §4.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Expected maximum drawdown for a 100% momentum book -- what this dashboard
#: actually displays. The 50/50 blend's -41.6% does NOT apply here, and the
#: drawdown chart is scaled against this figure for the same reason.
MOMENTUM_MAX_DRAWDOWN = -0.531


@dataclass(frozen=True, slots=True)
class Expectation:
    label: str
    value: str
    note: str
    #: Which book the figure describes. Shown, because a 50/50 number beside a
    #: momentum-only NAV is not a comparison, it is a category error.
    allocation: str = "this sleeve"


EXPECTED_PAIN: tuple[Expectation, ...] = (
    Expectation("Maximum drawdown", "−53.1%",
                "the momentum sleeve alone, over the archive",
                "100% momentum"),
    Expectation("Longest lag vs NIFTY", "1,203 days",
                "3.3 years of trailing the index is the expected case",
                "100% momentum"),
    Expectation("Longest drawdown", "1,027 days",
                "2.8 years below the previous peak",
                "50/50 blend"),
    Expectation("Worst relative shortfall", "−39.7%",
                "over 39.5 months, starting January 2018",
                "50/50 blend"),
)

#: Why two allocations appear at all.
SLEEVE_NOTE = (
    "This page values the momentum sleeve only — the NIFTYBEES half is held "
    "manually and is not tracked here. Blending it in is what cuts the "
    "expected worst case from −53.1% to −41.6%, so the whole-account pain is "
    "gentler than the figures above."
)

#: Rule 4, quoted rather than paraphrased. It is the one that costs the most to
#: break: selling the book costs 33-40% of terminal wealth whenever it is done,
#: and unlike every other panic response it does not get cheaper by waiting.
RULE = (
    "Existing positions are never liquidated because of drawdown. "
    "Underperformance versus NIFTY is not a reason to sell — not at −10%, "
    "not at −30%, not after three years."
)

POLICY_PATH = "INVESTMENT-POLICY.md"
