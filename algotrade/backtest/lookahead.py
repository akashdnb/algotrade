"""Mechanical look-ahead detection (PROJECT.md §6, test #1).

You *will* introduce look-ahead bias. Everyone does, and the results still look
plausible afterwards, which is what makes it dangerous. So detection has to be
mechanical rather than disciplinary.

The principle: a feature computed at time `t` may use only data with timestamp
<= `t`. So compute it twice -- once on the full panel, once on a panel truncated
at `T` -- and compare the rows at dates <= `T`. If they differ, the function
peeked at the future, because truncation removed something it was reading.

    full[date <= T]  ==  truncated_at_T[date <= T]

This catches the whole family of mistakes at once: a centred rolling window, a
forward shift with the wrong sign, a groupwise mean over the entire history, a
normalisation using full-sample statistics. None of those are visible by reading
the code carefully; all of them fail this test immediately.

**Two blind spots it used to have, both now closed:**

*Closed-over state.* The detector re-invokes `feature_fn(panel)`, so anything
captured in the closure is invariant to truncation and slips through. That is the
exact shape of `run_hypothesis.load_adjusted_panel`: corporate-action factors are
derived from the *whole* panel, and every back-adjusted price *level* therefore
embeds all future splits. Ratio features cancel it; level and volume features do
not. Pass `rebuild` so such inputs are re-derived per cutoff.

*Late-history peeks.* Default cutoffs sat at 1/4, 2/4 and 3/4, so nothing after
the last one was ever compared -- a peek confined to the final quarter reported
clean. Realistic, because `deliv_pct` is null before ~2020, so any delivery
feature effectively lives only in the recent era. A cutoff near the end is now
included by default.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Sequence

import polars as pl

FeatureFn = Callable[[pl.DataFrame], pl.DataFrame]


@dataclass
class LookaheadReport:
    """Outcome of a truncation check. `clean` is the only acceptable result."""

    clean: bool
    checked_cutoffs: list[date] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        if self.clean:
            return f"clean: {len(self.checked_cutoffs)} cutoff(s) matched exactly"
        lines = [f"LOOK-AHEAD DETECTED in {len(self.failures)} cutoff(s):"]
        for f in self.failures[:5]:
            lines.append(
                f"  cutoff {f['cutoff']}: {f['mismatched_rows']:,} of "
                f"{f['compared_rows']:,} rows differ in {f['columns']}"
            )
        return "\n".join(lines)


def _compare(
    full: pl.DataFrame,
    truncated: pl.DataFrame,
    keys: Sequence[str],
    columns: Sequence[str],
    cutoff: date,
    tol: float,
) -> dict | None:
    """Compare overlapping rows; return a failure record or None if identical."""
    left = full.filter(pl.col("date") <= cutoff).sort(list(keys))
    right = truncated.filter(pl.col("date") <= cutoff).sort(list(keys))

    if left.height != right.height:
        return {
            "cutoff": cutoff,
            "compared_rows": left.height,
            "mismatched_rows": abs(left.height - right.height),
            "columns": ["<row count>"],
            "detail": f"{left.height} vs {right.height} rows",
        }

    joined = left.join(right, on=list(keys), how="inner", suffix="_trunc")
    bad_columns: list[str] = []
    worst = 0
    for col in columns:
        other = f"{col}_trunc"
        if other not in joined.columns:
            bad_columns.append(col)
            continue
        a, b = pl.col(col), pl.col(other)
        # Null-vs-null counts as equal; null-vs-value does not.
        differs = (
            (a.is_null() != b.is_null())
            | (a.is_not_null() & b.is_not_null() & ((a - b).abs() > tol))
        )
        n = int(joined.select(differs.sum()).item() or 0)
        if n:
            bad_columns.append(col)
            worst = max(worst, n)

    if not bad_columns:
        return None
    return {
        "cutoff": cutoff,
        "compared_rows": joined.height,
        "mismatched_rows": worst,
        "columns": bad_columns,
        "detail": "values differ on overlapping dates",
    }


def check(
    panel: pl.DataFrame,
    feature_fn: FeatureFn,
    *,
    columns: Sequence[str],
    cutoffs: Sequence[date] | None = None,
    n_cutoffs: int = 3,
    keys: Sequence[str] = ("date", "symbol"),
    tol: float = 1e-9,
    rebuild: Callable[[pl.DataFrame], dict] | None = None,
) -> LookaheadReport:
    """Check `feature_fn` for look-ahead by truncating the panel.

    Args:
        feature_fn: Takes a panel, returns it with feature columns added. Must
            be deterministic, since the test compares two invocations.
        columns: The feature columns to compare.
        cutoffs: Truncation dates. Defaults to evenly spaced interior sessions
            **plus one near the end**, so a peek confined to late history cannot
            hide behind the last cutoff.
        rebuild: Given the truncated panel, returns kwargs for `feature_fn`.
            **Pass this whenever the feature depends on anything derived from the
            panel** -- corporate-action factors, universe membership, a fitted
            scaler. Without it those inputs stay pinned to the full history and
            the check cannot see them.

    Returns:
        A report whose `clean` flag is the thing to assert on.
    """
    sessions = panel["date"].unique().sort().to_list()
    if len(sessions) < 4:
        raise ValueError("need at least 4 sessions to truncate meaningfully")

    if cutoffs is None:
        # Interior points, plus one two sessions from the end so a late-history
        # peek has somewhere to be caught.
        step = len(sessions) // (n_cutoffs + 1)
        cutoffs = [sessions[step * (i + 1)] for i in range(n_cutoffs)]
        late = sessions[-3]
        if late not in cutoffs:
            cutoffs = [*cutoffs, late]

    def _invoke(frame: pl.DataFrame) -> pl.DataFrame:
        return feature_fn(frame, **rebuild(frame)) if rebuild else feature_fn(frame)

    full = _invoke(panel)
    report = LookaheadReport(clean=True, checked_cutoffs=list(cutoffs))

    for cutoff in cutoffs:
        truncated = _invoke(panel.filter(pl.col("date") <= cutoff))
        failure = _compare(full, truncated, keys, columns, cutoff, tol)
        if failure is not None:
            report.clean = False
            report.failures.append(failure)

    return report


def assert_no_lookahead(
    panel: pl.DataFrame, feature_fn: FeatureFn, *, columns: Sequence[str], **kwargs
) -> None:
    """Raise if `feature_fn` peeks at the future. For use in tests."""
    report = check(panel, feature_fn, columns=columns, **kwargs)
    if not report.clean:
        raise AssertionError(report.summary())
