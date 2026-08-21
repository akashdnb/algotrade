"""Time-sliced evaluation across folds (PROJECT.md §8.3, §8.1's regime gate).

**Read this before calling it "out-of-sample".** In this project parameters come
from reasoning, not fitting (§8.4): a 5-day lookback because that is a week, 12-1
momentum because that is the literature standard. When nothing is fitted,
splitting the data does not create out-of-sampleness -- every period is equally
"not fitted", and a fold boundary changes nothing about how the numbers were
arrived at.

So this module does **not** manufacture an out-of-sample claim. What it measures
is **stability across time**, which is a different and still valuable thing:

* Does the result hold in each sub-period, or is it one good regime carrying a
  decade? That is §8.1's "profitable across >= 2 distinct regimes" gate, which was
  specified and never implemented.
* How much does the estimate move between folds? Fold dispersion is a far more
  honest error bar than a single full-period Sharpe with no interval at all.

The residual dishonesty this cannot fix: the *unlogged* choices -- universe size
300, lookback 20, coverage 0.80, `min_ratio` 1.75, slippage k=0.6, `n_side` 15,
cadence -- were all made with sight of the same history. Folds do not separate
those from the data used to evaluate them. Only the untouched holdout does, and
only for as many looks as remain.

The purge gap exists so a fold's features do not read across its own boundary.
Features are computed on the whole panel and then sliced, so a test period
starting at T has features looking back into T-1's data; purging the first
`purge` sessions of each test window removes those.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Callable, Sequence

import polars as pl

from algotrade.backtest import metrics


@dataclass(frozen=True)
class Fold:
    """One evaluation window. `start` is already purged."""

    index: int
    start: date
    end: date
    purged_from: date

    def label(self) -> str:
        return f"fold {self.index}: {self.start} .. {self.end}"


def make_folds(
    sessions: Sequence[date],
    *,
    n_folds: int = 5,
    purge: int = 0,
    min_sessions: int = 250,
) -> list[Fold]:
    """Split a session list into `n_folds` contiguous windows.

    Args:
        purge: Sessions dropped from the *start* of each window, so a fold's
            features cannot read across its own left boundary. Set this to the
            feature's total lookback (252 + 21 = 273 for 12-1 momentum).
        min_sessions: Folds shorter than this after purging are discarded --
            a 60-session fold produces a Sharpe with a standard error near 2.

    Raises:
        ValueError: if the data cannot support the requested folds.
    """
    ordered = sorted(set(sessions))
    if n_folds < 1:
        raise ValueError("n_folds must be >= 1")
    per_fold = len(ordered) // n_folds
    if per_fold <= purge + min_sessions:
        raise ValueError(
            f"{len(ordered)} sessions cannot support {n_folds} folds with "
            f"purge={purge} and min_sessions={min_sessions}: each fold would "
            f"hold {per_fold} sessions"
        )

    folds: list[Fold] = []
    for i in range(n_folds):
        lo = i * per_fold
        hi = len(ordered) if i == n_folds - 1 else (i + 1) * per_fold
        window = ordered[lo:hi]
        if len(window) <= purge + min_sessions:
            continue
        folds.append(
            Fold(
                index=len(folds) + 1,
                start=window[purge],
                end=window[-1],
                purged_from=window[0],
            )
        )
    return folds


@dataclass
class FoldResult:
    fold: Fold
    periods: int
    cagr: float
    volatility: float
    sharpe: float
    max_drawdown: float


@dataclass
class Report:
    """Per-fold results plus the stability summary that §8.1 asks for."""

    folds: list[FoldResult]
    combined: pl.DataFrame

    @property
    def positive_folds(self) -> int:
        return sum(1 for f in self.folds if f.cagr > 0)

    @property
    def sharpe_spread(self) -> float:
        """Max minus min fold Sharpe -- a blunt but honest error bar."""
        if len(self.folds) < 2:
            return 0.0
        values = [f.sharpe for f in self.folds]
        return max(values) - min(values)

    def stable(self, *, min_positive_fraction: float = 0.6) -> bool:
        """Whether the result holds across time rather than in one regime."""
        if not self.folds:
            return False
        return self.positive_folds / len(self.folds) >= min_positive_fraction

    def summary(self) -> str:
        lines = [
            f"{'fold':<6}{'sessions':>10}{'CAGR':>9}{'vol':>8}"
            f"{'Sharpe':>9}{'maxDD':>9}",
        ]
        for f in self.folds:
            lines.append(
                f"{f.fold.index:<6}{f.periods:>10,}{f.cagr:>9.1%}"
                f"{f.volatility:>8.1%}{f.sharpe:>9.2f}{f.max_drawdown:>9.1%}"
            )
        lines.append(
            f"\npositive folds  {self.positive_folds}/{len(self.folds)}"
            f"    Sharpe spread  {self.sharpe_spread:.2f}"
        )
        lines.append(
            "NOTE: this measures stability across time, not out-of-sample "
            "performance -- nothing was fitted, so folds add no independence."
        )
        return "\n".join(lines)


def evaluate(
    equity: pl.DataFrame,
    *,
    n_folds: int = 5,
    purge: int = 0,
    min_sessions: int = 250,
    risk_free: float = 0.0,
) -> Report:
    """Slice an equity curve into folds and measure each independently.

    Args:
        risk_free: Defaults to 0, the correct choice for a dollar-neutral
            long/short book whose return is already a self-financing spread.

    The combined frame is the concatenation of the *purged* folds, so it excludes
    the boundary sessions -- it is not the same series as the full-period run and
    should not be compared to it directly.
    """
    if equity.is_empty():
        return Report(folds=[], combined=equity)

    sessions = equity["date"].unique().sort().to_list()
    folds = make_folds(
        sessions, n_folds=n_folds, purge=purge, min_sessions=min_sessions
    )

    results: list[FoldResult] = []
    kept: list[pl.DataFrame] = []
    for fold in folds:
        window = equity.filter(
            pl.col("date").is_between(fold.start, fold.end)
        ).sort("date")
        if window.is_empty():
            continue
        m = metrics.compute(window, risk_free=risk_free)
        results.append(
            FoldResult(
                fold=fold,
                periods=m.periods,
                cagr=m.cagr,
                volatility=m.volatility,
                sharpe=m.sharpe,
                max_drawdown=m.max_drawdown,
            )
        )
        kept.append(window)

    combined = pl.concat(kept) if kept else equity.head(0)
    return Report(folds=results, combined=combined)
