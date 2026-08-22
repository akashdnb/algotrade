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

    # Row counts can match while the KEY SETS differ -- a pipeline that swaps one
    # held name for another emits the same number of rows. Comparing only the
    # intersection then finds two identical weights and reports clean, which is
    # how a full-history exclusion list slipped through: it changes *which*
    # symbols appear, not how many.
    if joined.height != left.height:
        return {
            "cutoff": cutoff,
            "compared_rows": left.height,
            "mismatched_rows": left.height - joined.height,
            "columns": ["<membership>"],
            "detail": f"{left.height - joined.height} key(s) present in one run only",
        }

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


def _month_end_cutoffs(sessions: list[date], n: int) -> list[date]:
    """`n` evenly spaced cutoffs, each snapped to the last session of its month.

    Truncating mid-month is what forced the old `settle` blindfold: the truncated
    run sees its final session as a completed month and rebalances there. Snap to
    a real month end and both runs agree, so the comparison can be exact.
    """
    ends = sorted(
        pl.DataFrame({"date": sessions})
        .group_by_dynamic("date", every="1mo")
        .agg(pl.col("date").max().alias("d"))
        .get_column("d")
        .to_list()
    )
    # Skip the first and last month: the first has no history to compare and the
    # last is the full panel, so neither can fail.
    inner = ends[1:-1]
    if not inner:
        step = len(sessions) // (n + 1)
        return [sessions[step * (i + 1)] for i in range(n)]
    step = max(len(inner) // (n + 1), 1)
    picked = [inner[min(step * (i + 1), len(inner) - 1)] for i in range(n)]
    return sorted(set(picked))


def check_pipeline(
    raw: pl.DataFrame,
    pipeline_fn: Callable[[pl.DataFrame], pl.DataFrame],
    *,
    cutoffs: Sequence[date] | None = None,
    n_cutoffs: int = 3,
    tol: float = 1e-9,
    settle: int = 0,
) -> LookaheadReport:
    """Check a whole research pipeline, not just a feature (§11h, R2b).

    `check` only ever re-invokes `feature_fn`, so every leak the second audit
    found lived *outside* it and was structurally invisible: an exclusion list
    built from full history, a regime mask, universe membership, anything
    derived from the panel before the feature is computed.

    This truncates the **raw** panel and compares the resulting **weights** for
    dates <= T. Weights are the right comparison point because they are the
    pipeline's actual output, and because they come from cross-sectional *ranks*
    of ratio features -- which are invariant to back-adjustment. A correct
    pipeline therefore produces byte-identical weights on the overlap even
    though a future split restates historical price levels.

    Args:
        pipeline_fn: raw panel -> (date, symbol, weight). Must derive everything
            from its argument; anything read from an enclosing scope is exactly
            what this is built to catch.
        cutoffs: Where to truncate. **Defaults to calendar month ends**, which
            matters: cutting mid-month makes the truncated run treat its own last
            session as a month end -- `group_by_dynamic` cannot know the month is
            incomplete -- so an extra rebalance fires there and the held set
            legitimately differs. Cutting at a true month end removes the
            artefact at source. Cutting at 2019-01-03 produced 9 spurious
            membership differences; the same pipeline cut at month ends is clean.
        settle: Sessions immediately before each cutoff to ignore. **Defaults to
            0, and raising it is dangerous.** A k-session forward peek only
            perturbs rows dated > cutoff - k, so `settle` deletes from the
            comparison exactly the band where a leak shows: at the old default of
            25 a literal `close.shift(-1)` -- the fill-at-close leak this engine
            calls its single most consequential choice -- reported CLEAN, and so
            did every peek up to 25 sessions. Even `settle=2` hides a 1-day leak.
            Use month-end cutoffs to kill the artefact instead of blindfolding
            the check.
    """
    sessions = raw["date"].unique().sort().to_list()
    if len(sessions) < 8:
        raise ValueError("need at least 8 sessions to truncate a pipeline")

    if cutoffs is None:
        cutoffs = _month_end_cutoffs(sessions, n_cutoffs)

    full = pipeline_fn(raw)
    report = LookaheadReport(clean=True, checked_cutoffs=list(cutoffs))

    for cutoff in cutoffs:
        truncated = pipeline_fn(raw.filter(pl.col("date") <= cutoff))
        # Compare strictly before the settle window.
        idx = sessions.index(cutoff)
        horizon = sessions[max(0, idx - settle)]
        failure = _compare(
            full, truncated, ("date", "symbol"), ("weight",), horizon, tol
        )
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
