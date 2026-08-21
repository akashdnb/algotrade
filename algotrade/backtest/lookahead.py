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
) -> LookaheadReport:
    """Check `feature_fn` for look-ahead by truncating the panel.

    Args:
        feature_fn: Takes a panel, returns it with feature columns added. Must
            be deterministic, since the test compares two invocations.
        columns: The feature columns to compare.
        cutoffs: Truncation dates. Defaults to evenly spaced interior sessions,
            avoiding the extremes where there is nothing to compare or nothing
            to remove.

    Returns:
        A report whose `clean` flag is the thing to assert on.
    """
    sessions = panel["date"].unique().sort().to_list()
    if len(sessions) < 4:
        raise ValueError("need at least 4 sessions to truncate meaningfully")

    if cutoffs is None:
        # Interior points only: the first has no history, the last removes nothing.
        step = len(sessions) // (n_cutoffs + 1)
        cutoffs = [sessions[step * (i + 1)] for i in range(n_cutoffs)]

    full = feature_fn(panel)
    report = LookaheadReport(clean=True, checked_cutoffs=list(cutoffs))

    for cutoff in cutoffs:
        truncated = feature_fn(panel.filter(pl.col("date") <= cutoff))
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
