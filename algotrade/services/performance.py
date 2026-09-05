"""Performance metrics from the snapshot series.

Two return figures, always both, because they answer different questions and
diverge materially for a monthly contributor (§11n):

* **TWR** from `unit_nav` -- how did the *strategy* do, ignoring when money
  arrived. The right number against an index.
* **XIRR** from the cash ledger -- what did *you* actually earn. §11n's
  decision turned on this one.

Risk is reported as **Martin** (return per unit of ulcer) alongside Sharpe.
§11q found Sharpe on a long-only book had been computed without deducting the
risk-free rate, and that the resulting ranking reversed once fixed -- so `rf`
is explicit here and Martin, which the deployment decision actually used, is
first-class rather than an afterthought.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from algotrade.domain import money, valuation as val

log = logging.getLogger(__name__)

#: Indian risk-free proxy. MUST be deducted for a long-only book -- rf=0 is
#: valid only for a self-financing dollar-neutral spread (§11q F1).
RISK_FREE: float = 0.065

SESSIONS_PER_YEAR: int = 250

#: Annualising a handful of sessions produces arithmetically correct nonsense
#: -- a -0.67% move over three days annualises to -70% CAGR. Below this, the
#: annualised figures are reported as None rather than as an alarming number
#: nobody should act on.
MIN_SESSIONS_TO_ANNUALISE: int = 60

#: XIRR is gated on elapsed DAYS, not session count.
#:
#: It is solved from cash flows, which need not be daily, so "how many
#: snapshots exist" is the wrong proxy for "has enough time passed" -- two
#: readings a year apart annualise perfectly well. What makes an annual rate
#: meaningless is a short *span*, and a quarter is the shortest that is not
#: simply extrapolation.
MIN_DAYS_TO_ANNUALISE: int = 90


@dataclass(frozen=True, slots=True)
class Performance:
    portfolio_id: str
    start: date
    end: date
    sessions: int

    start_unit_nav: Decimal
    end_unit_nav: Decimal

    total_return: float | None
    cagr: float | None
    xirr: float | None

    max_drawdown: float
    current_drawdown: float
    ulcer_index: float
    volatility: float | None
    sharpe: float | None
    martin: float | None

    win_rate: float | None
    best_day: float | None
    worst_day: float | None

    benchmark_return: float | None = None
    excess_return: float | None = None

    def summary(self) -> dict:
        return {
            "sessions": self.sessions,
            "total_return": self.total_return,
            "cagr": self.cagr,
            "xirr": self.xirr,
            "max_drawdown": self.max_drawdown,
            "sharpe": self.sharpe,
            "martin": self.martin,
        }


class PerformanceService:
    def __init__(self, *, snapshots, cash) -> None:
        self._snapshots = snapshots
        self._cash = cash

    def compute(
        self,
        portfolio_id: str,
        *,
        start: date | None = None,
        end: date | None = None,
        risk_free: float = RISK_FREE,
    ) -> Performance | None:
        """Metrics over the snapshot series. None if there is nothing to measure."""
        rows = self._snapshots.series(
            portfolio_id, start=start, end=end,
            fields=["unit_nav", "peak_unit_nav", "drawdown", "nav",
                    "benchmark_close"],
        )
        if len(rows) < 1:
            return None

        navs = [r["unit_nav"] for r in rows if r.get("unit_nav")]
        dates = [_as_date(r["date"]) for r in rows]
        if not navs:
            return None

        first, last = navs[0], navs[-1]
        span_days = (dates[-1] - dates[0]).days

        returns = [
            float((navs[i] - navs[i - 1]) / navs[i - 1])
            for i in range(1, len(navs)) if navs[i - 1] > 0
        ]

        # From the BASE unit NAV, not from the first stored snapshot.
        #
        # Unit NAV is 100.00 at inception by construction (`units` is
        # `initial_capital / BASE_UNIT_NAV`), and the first snapshot is written
        # at the END of the first valued session -- so measuring from it
        # silently drops that session's move. Here that was 1.63%, and it put
        # "+1.18%" on the History page beside "-0.47%" on Summary for the same
        # portfolio on the same day. Two numbers, one of which nobody wanted.
        #
        # A caller that asks for a window gets a window-relative figure,
        # because base 100 is not that window's starting point. Nothing does
        # today; every call site is `compute(portfolio_id)`.
        base = first if start is not None else val.BASE_UNIT_NAV
        total_return = float((last - base) / base) if base > 0 else None
        annualisable = len(navs) >= MIN_SESSIONS_TO_ANNUALISE and span_days > 0
        cagr = (
            val.twr(first_unit_nav=first, last_unit_nav=last,
                    start=dates[0], end=dates[-1])
            if annualisable else None
        )

        # Drawdown from the unit-NAV series itself, so contributions cannot
        # mask a decline the way a raw NAV series would.
        peak, max_dd, squared = navs[0], 0.0, []
        for nav in navs:
            peak = max(peak, nav)
            dd = float((nav - peak) / peak) if peak > 0 else 0.0
            max_dd = min(max_dd, dd)
            squared.append((dd * 100) ** 2)
        ulcer = math.sqrt(sum(squared) / len(squared)) if squared else 0.0
        current_dd = float((last - peak) / peak) if peak > 0 else 0.0

        volatility = None
        sharpe = None
        if len(returns) > 1:
            mean = sum(returns) / len(returns)
            variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
            volatility = (math.sqrt(variance) * math.sqrt(SESSIONS_PER_YEAR)
                          if annualisable else None)
            if volatility and volatility > 0 and cagr is not None:
                # rf DEDUCTED. §11q F1: omitting it on a long-only book
                # inflated every Sharpe and reversed the ranking.
                sharpe = (cagr - risk_free) / volatility

        martin = (
            (cagr - risk_free) / (ulcer / 100)
            if cagr is not None and ulcer > 0 else None
        )

        flows = list(self._cash.contributions(portfolio_id))
        terminal = rows[-1].get("nav")
        xirr = None
        # `val.xirr` solves for an ANNUAL rate, so a fortnight of data gives a
        # figure like -23.8%: arithmetically correct and meaningless. It was
        # ungated until 2026-09-01, which left the one annualised number that
        # survived the short-sample guard being the one a person takes most
        # personally -- "what YOU earned".
        if flows and terminal and span_days >= MIN_DAYS_TO_ANNUALISE:
            # Sign convention: money in is negative, terminal value positive.
            xirr = val.xirr(
                [(d, money.rupees(-a)) for d, a in flows]
                + [(dates[-1], terminal)]
            )

        benchmark_return = _benchmark_return(rows)
        return Performance(
            portfolio_id=portfolio_id, start=dates[0], end=dates[-1],
            sessions=len(rows),
            start_unit_nav=first, end_unit_nav=last,
            total_return=total_return, cagr=cagr, xirr=xirr,
            max_drawdown=max_dd, current_drawdown=current_dd,
            ulcer_index=ulcer, volatility=volatility,
            sharpe=sharpe, martin=martin,
            win_rate=(sum(1 for r in returns if r > 0) / len(returns)
                      if returns else None),
            best_day=max(returns) if returns else None,
            worst_day=min(returns) if returns else None,
            benchmark_return=benchmark_return,
            excess_return=(total_return - benchmark_return
                           if total_return is not None
                           and benchmark_return is not None else None),
        )

    def turnover(self, portfolio_id: str, *, start: date, end: date,
                 executions) -> float | None:  # noqa: ANN001
        """Traded value over average book value, annualised by calendar span."""
        fills = executions.between(portfolio_id, start=start, end=end)
        if not fills:
            return 0.0
        traded = money.sum_rupees(e.value for e in fills)
        rows = self._snapshots.series(portfolio_id, start=start, end=end,
                                      fields=["nav"])
        navs = [r["nav"] for r in rows if r.get("nav")]
        if not navs:
            return None
        average = money.rupees(sum(navs) / len(navs))
        if average == 0:
            return None
        years = max((end - start).days, 1) / 365.25
        return float(traded / average) / years


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _benchmark_return(rows: list[dict]) -> float | None:
    closes = [r["benchmark_close"] for r in rows if r.get("benchmark_close")]
    if len(closes) < 2 or closes[0] == 0:
        return None
    return float((closes[-1] - closes[0]) / closes[0])
