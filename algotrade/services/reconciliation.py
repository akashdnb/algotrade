"""Mechanical consistency checks. Prefer reconciliation over trust.

Every derived value in this system has an independent path back to a primary
record, and this module walks those paths and compares. It exists because four
audits found ~55 defects, and every one of them was a number that looked
plausible.

Four checks, each comparing a *cache* against its *authority*:

| Cache                       | Authority          |
|-----------------------------|--------------------|
| `holdings`                  | `executions`       |
| `portfolios.cash`           | `cash_transactions`|
| `recommendations.items.*`   | `executions`       |
| `portfolio_snapshots`       | recomputation      |

A failure here is not a warning. A book that disagrees with its trade record
means every valuation derived from it is wrong, and the correct response is to
stop rather than to publish a number.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from algotrade.domain import money

log = logging.getLogger(__name__)

#: Rupee tolerance. Anything larger is a real disagreement, not rounding.
TOLERANCE: Decimal = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Finding:
    check: str
    severity: str          # error | warning | info
    message: str

    def __str__(self) -> str:
        mark = {"error": "FAIL", "warning": "WARN", "info": "OK  "}[self.severity]
        return f"[{mark}] {self.check}: {self.message}"


@dataclass(slots=True)
class Report:
    portfolio_id: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_inconsistent(self) -> None:
        if self.ok:
            return
        detail = "\n".join(str(f) for f in self.errors)
        raise InconsistentState(
            f"{self.portfolio_id}: {len(self.errors)} inconsistency(ies)\n{detail}"
        )


class InconsistentState(RuntimeError):
    """A derived value disagrees with its authority."""


class ReconciliationService:
    def __init__(
        self, *, portfolios, holdings_service, cash, executions,
        recommendations, snapshots,
    ) -> None:
        self._portfolios = portfolios
        self._holdings_service = holdings_service
        self._cash = cash
        self._executions = executions
        self._recs = recommendations
        self._snapshots = snapshots

    def check(self, portfolio_id: str, *, as_of: date | None = None) -> Report:
        report = Report(portfolio_id=portfolio_id)
        report.findings.extend(self._check_holdings(portfolio_id))
        report.findings.extend(self._check_cash(portfolio_id, as_of))
        report.findings.extend(self._check_rollups(portfolio_id))
        report.findings.extend(self._check_snapshot(portfolio_id))
        return report

    # ------------------------------------------- holdings vs executions -----

    def _check_holdings(self, portfolio_id: str) -> list[Finding]:
        problems = self._holdings_service.verify(portfolio_id)
        if problems:
            return [
                Finding("holdings", "error",
                        f"{len(problems)} position(s) disagree with the trade "
                        f"record: {'; '.join(problems[:3])}")
            ]
        return [Finding("holdings", "info", "match the execution history")]

    # ----------------------------------------------- cash vs the ledger -----

    def _check_cash(self, portfolio_id: str, as_of: date | None) -> list[Finding]:
        ledger = self._cash.balance(portfolio_id, through=as_of)
        portfolio = self._portfolios.get(portfolio_id)
        if portfolio is None:
            return [Finding("cash", "error", "portfolio not found")]

        cached = portfolio.get("cash") or money.ZERO
        out = []
        if abs(cached - ledger) > TOLERANCE:
            out.append(Finding(
                "cash", "error",
                f"portfolios.cash is {cached} but the ledger sums to {ledger} "
                f"(difference {money.rupees(cached - ledger)})",
            ))
        else:
            out.append(Finding("cash", "info", f"cached balance matches ledger ({ledger})"))

        # Every execution must have produced exactly one ledger entry.
        recorded = self._cash.execution_ids(portfolio_id)
        all_fills = {
            e.execution_id
            for e in self._executions.between(
                portfolio_id, start=date(1990, 1, 1), end=date(2100, 1, 1))
        }
        missing = all_fills - recorded
        if missing:
            out.append(Finding(
                "cash.ledger", "error",
                f"{len(missing)} execution(s) moved no cash: "
                f"{sorted(missing)[:3]}",
            ))
        orphaned = recorded - all_fills
        if orphaned:
            out.append(Finding(
                "cash.ledger", "error",
                f"{len(orphaned)} ledger entry(ies) reference a missing "
                f"execution: {sorted(orphaned)[:3]}",
            ))
        if not missing and not orphaned:
            out.append(Finding(
                "cash.ledger", "info",
                f"{len(all_fills)} execution(s) each have one ledger entry"))
        return out

    # --------------------------------------- rollups vs executions ----------

    def _check_rollups(self, portfolio_id: str) -> list[Finding]:
        from algotrade.domain.models import reconcile_item_rollup

        problems: list[str] = []
        checked = 0
        for doc in self._recs.history(portfolio_id, limit=24):
            rec = self._recs.get_set(doc["_id"])
            if rec is None:
                continue
            executions = self._executions.for_set(rec.set_id)
            for item in rec.items:
                checked += 1
                problems.extend(
                    f"{rec.set_id}: {p}"
                    for p in reconcile_item_rollup(item, executions)
                )
        if problems:
            return [Finding("rollups", "error",
                            f"{len(problems)} item(s) disagree with their "
                            f"executions: {'; '.join(problems[:3])}")]
        return [Finding("rollups", "info",
                        f"{checked} recommendation item(s) match their executions")]

    # ------------------------------------------------ snapshot sanity -------

    def _check_snapshot(self, portfolio_id: str) -> list[Finding]:
        latest = self._snapshots.latest(portfolio_id)
        if latest is None:
            return [Finding("snapshot", "warning", "no snapshot yet")]

        out = []
        # NAV must equal what its own parts add up to. A snapshot that fails
        # this was written by code that computed the total separately from the
        # components -- which is how a plausible wrong number gets published.
        implied = money.rupees(latest.invested_value + latest.cash)
        if abs(implied - latest.nav) > TOLERANCE:
            out.append(Finding(
                "snapshot", "error",
                f"{latest.date}: nav {latest.nav} != invested "
                f"{latest.invested_value} + cash {latest.cash} = {implied}",
            ))

        positions_total = money.sum_rupees(p.value for p in latest.positions)
        if abs(positions_total - latest.invested_value) > TOLERANCE:
            out.append(Finding(
                "snapshot", "error",
                f"{latest.date}: positions sum to {positions_total} but "
                f"invested_value is {latest.invested_value}",
            ))

        if latest.units > 0:
            implied_nav = money.rupees(latest.unit_nav * latest.units)
            if abs(implied_nav - latest.nav) > Decimal("1.00"):
                out.append(Finding(
                    "snapshot", "error",
                    f"{latest.date}: unit_nav x units = {implied_nav}, "
                    f"nav = {latest.nav}",
                ))
        if not out:
            out.append(Finding("snapshot", "info",
                               f"{latest.date} is internally consistent"))
        return out
