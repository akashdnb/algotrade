"""Drawdown shape, not just drawdown depth.

Maximum drawdown is one number describing one day, and it hides everything an
investor actually experiences: how often the book is underwater, how long it
stays there, and whether the pain is the market's or the strategy's own. Two
books with the same -50% max drawdown are different propositions if one recovers
in four months and the other takes three years.

Everything here is pure: a capital series in, statistics out. No I/O, no engine.

Conventions, stated because they differ between sources:

* An **episode** runs from the session after a running peak is set, through the
  trough, to the session the peak is first regained. An episode still underwater
  at the end of the sample is reported with `recovered=False` and a null
  recovery date -- never silently closed at the last session, which would flatter
  the longest-recovery figure exactly when it matters most.
* **Time underwater** counts sessions strictly below the running peak.
* **Ulcer index** is the RMS of percentage drawdown over every session, in
  percent. Unlike max drawdown it charges for duration, and unlike volatility it
  ignores upside.
* **Downside capture** is the ratio of *mean* returns on the benchmark's down
  sessions. Compounding instead -- linking 1,000+ non-contiguous down days into
  one cumulative figure -- is degenerate: both books converge on -100%, so the
  ratio collapses to 1.00 regardless of what either did. It reported exactly
  1.00 for a beta-0.71 book and a beta-1.07 book alike. Use `capture_by_period`
  for the compounded form, which is only meaningful over contiguous periods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import polars as pl


@dataclass(frozen=True)
class Episode:
    """One peak-to-peak underwater stretch."""

    start: date        # first session below the peak
    trough: date       # session of maximum depth
    recovery: date | None   # first session back at the peak; None if ongoing
    depth: float       # negative fraction
    duration: int      # sessions from start to recovery (or sample end)
    to_trough: int     # sessions from start to trough
    to_recover: int | None  # sessions from trough to recovery
    recovered: bool

    @property
    def depth_pct(self) -> float:
        return abs(self.depth) * 100


def episodes(
    capital: pl.Series | pl.DataFrame,
    dates: pl.Series | None = None,
    *,
    threshold: float = 0.0,
) -> list[Episode]:
    """Every underwater stretch deeper than `threshold` (a positive fraction).

    Args:
        capital: Equity series, or a frame carrying `date` and `capital`.
        dates: Session dates, if `capital` is a bare series.
        threshold: Ignore episodes shallower than this. 0.0 returns everything,
            including one-session dips, which is rarely what you want for
            reporting but is the honest default for counting.
    """
    if isinstance(capital, pl.DataFrame):
        dates = capital["date"]
        capital = capital["capital"]
    if dates is None:
        raise ValueError("dates required when capital is a bare series")
    if capital.len() == 0:
        return []

    vals = capital.to_list()
    days = dates.to_list()
    out: list[Episode] = []

    peak = vals[0]
    peak_i = 0
    trough_i: int | None = None

    for i in range(1, len(vals)):
        v = vals[i]
        if v >= peak:
            if trough_i is not None:
                out.append(
                    _make(days, peak_i, trough_i, i, vals[trough_i] / peak - 1, True)
                )
                trough_i = None
            peak, peak_i = v, i
        else:
            if trough_i is None or v < vals[trough_i]:
                trough_i = i

    # An episode still open at the end is NOT closed at the last session. Doing
    # that reports a recovery that never happened and shortens the longest
    # recovery precisely when the book is in its worst state.
    if trough_i is not None:
        out.append(
            _make(days, peak_i, trough_i, len(vals) - 1,
                  vals[trough_i] / peak - 1, False)
        )

    return [e for e in out if abs(e.depth) > threshold]


def _make(days, peak_i, trough_i, end_i, depth, recovered) -> Episode:
    start_i = peak_i + 1
    return Episode(
        start=days[start_i],
        trough=days[trough_i],
        recovery=days[end_i] if recovered else None,
        depth=depth,
        duration=end_i - start_i + 1,
        to_trough=trough_i - start_i + 1,
        to_recover=(end_i - trough_i) if recovered else None,
        recovered=recovered,
    )


def underwater_fraction(capital: pl.Series) -> float:
    """Share of sessions strictly below the running peak."""
    if capital.len() == 0:
        return 0.0
    return float((capital < capital.cum_max()).mean() or 0.0)


def ulcer_index(capital: pl.Series) -> float:
    """RMS percentage drawdown across the whole series (Peter Martin, 1987).

    Charges for depth AND duration, where max drawdown charges only for depth.
    A book that dips 50% once and recovers immediately scores far better here
    than one that sits at -20% for three years, which is the distinction max
    drawdown cannot make.
    """
    if capital.len() == 0:
        return 0.0
    dd = (capital / capital.cum_max() - 1) * 100
    return float((dd**2).mean() ** 0.5)


def martin_ratio(cagr: float, capital: pl.Series, *, risk_free: float = 0.0) -> float:
    """Return per unit of ulcer. The drawdown-shaped analogue of Sharpe."""
    ui = ulcer_index(capital)
    return (cagr - risk_free) / (ui / 100) if ui > 0 else 0.0


def _capture(strategy: pl.Series, benchmark: pl.Series, *, down: bool) -> float:
    if strategy.len() != benchmark.len():
        raise ValueError("series must be aligned and equal length")
    mask = (benchmark < 0) if down else (benchmark > 0)
    if not bool(mask.any()):
        return float("nan")
    b = float(benchmark.filter(mask).mean() or 0.0)
    s = float(strategy.filter(mask).mean() or 0.0)
    return s / b if b != 0 else float("nan")


def downside_capture(strategy: pl.Series, benchmark: pl.Series) -> float:
    """Mean strategy return on the benchmark's DOWN sessions, relative to it.

    < 1.0 defensive, 1.0 market-like, > 1.0 amplifies declines.

    **Means, not compounding.** Chaining 1,000+ non-contiguous down days into a
    cumulative return sends both books to nearly -100%, and the ratio of two
    numbers approaching -1 is 1.00 whatever they did on the way: measured here,
    the compounded form scored a beta-0.71 book and a beta-1.07 book at exactly
    1.00 apiece. Compounding is only meaningful over contiguous windows -- see
    `capture_by_period`.

    Returns nan when the benchmark never falls, rather than a misleading 0.
    """
    return _capture(strategy, benchmark, down=True)


def upside_capture(strategy: pl.Series, benchmark: pl.Series) -> float:
    """The mirror. Reported alongside downside capture because a low downside
    capture bought with an equally low upside capture is just a smaller
    position, not a defensive one."""
    return _capture(strategy, benchmark, down=False)


def capture_by_period(
    dates: pl.Series,
    strategy: pl.Series,
    benchmark: pl.Series,
    *,
    every: str = "1mo",
    down: bool = True,
) -> float:
    """Capture computed over contiguous calendar periods, then averaged.

    The practitioner-standard form: compound each stream within a month, keep
    the months where the benchmark fell, and take the ratio of mean monthly
    returns. Compounding *within* a period is legitimate because the sessions
    are contiguous; compounding *across* selected down-days is not.
    """
    if not (dates.len() == strategy.len() == benchmark.len()):
        raise ValueError("series must be aligned and equal length")
    monthly = (
        pl.DataFrame({"date": dates, "s": strategy, "b": benchmark})
        .sort("date")
        .group_by_dynamic("date", every=every)
        .agg(
            ((1 + pl.col("s")).product() - 1).alias("s"),
            ((1 + pl.col("b")).product() - 1).alias("b"),
        )
    )
    return _capture(monthly["s"], monthly["b"], down=down)


def count_deeper_than(eps: list[Episode], *thresholds: float) -> dict[float, int]:
    """How many episodes exceeded each depth. Thresholds as positive fractions."""
    return {t: sum(1 for e in eps if abs(e.depth) > t) for t in thresholds}


def benchmark_move(
    bench: pl.DataFrame, start: date, end: date
) -> float:
    """The benchmark's own worst peak-to-trough decline inside a window.

    Used to ask whether a strategy episode was the market falling or the
    strategy falling. Compares like with like: the benchmark's drawdown over the
    same calendar span, not its point-to-point return, which would score a round
    trip as harmless.
    """
    sub = bench.filter(pl.col("date").is_between(start, end)).sort("date")
    if sub.height < 2:
        return 0.0
    return float((sub["capital"] / sub["capital"].cum_max() - 1).min())
