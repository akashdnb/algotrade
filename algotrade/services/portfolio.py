"""Portfolio creation and lookup.

Creation is **idempotent by natural key**: `portfolios._id` is the
`portfolio_id`, so a repeated `create` finds the existing document and returns
it rather than making a second one. There is no "create or overwrite" path --
overwriting a portfolio would silently reset a track record.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from algotrade.domain import ids, money
from algotrade.domain.models import (
    BenchmarkSleeve,
    ExecutionMode,
    Portfolio,
    PortfolioStatus,
)
from algotrade.repositories.base import AlreadyExists

log = logging.getLogger(__name__)


class PortfolioConflict(RuntimeError):
    """A portfolio exists but disagrees with what was requested.

    Distinct from "already exists": an idempotent re-create is fine, but one
    asking for different capital or a different strategy version is a mistake
    that must not be silently ignored.
    """


@dataclass(frozen=True, slots=True)
class CreateResult:
    portfolio: Portfolio
    created: bool          # False = it already existed and matched


class PortfolioService:
    def __init__(self, *, repository, audit=None) -> None:
        self._repo = repository
        self._audit = audit

    def create(
        self,
        *,
        portfolio_id: str,
        label: str,
        strategy_params_hash: str,
        initial_capital: Decimal,
        now: datetime,
        strategy: str = "momentum",
        monthly_contribution: Decimal = money.ZERO,
        contribution_day: int = 1,
        execution_mode: ExecutionMode = ExecutionMode.MANUAL,
        benchmark_sleeve: BenchmarkSleeve | None = None,
        cost_model: str = "",
        code_commit: str = "",
        account_note: str = "",
    ) -> CreateResult:
        """Create the portfolio, or return the existing one if it matches.

        Raises:
            ids.InvalidPortfolioId: malformed id.
            PortfolioConflict: exists but with different terms.
        """
        pid = ids.parse(portfolio_id)          # raises on a malformed id
        if pid.strategy != strategy:
            raise PortfolioConflict(
                f"id says strategy {pid.strategy!r} but {strategy!r} was requested"
            )

        existing = self._repo.get(portfolio_id)
        if existing is not None:
            self._assert_matches(
                existing,
                strategy_params_hash=strategy_params_hash,
                initial_capital=initial_capital,
            )
            log.info("portfolio %s already exists; nothing to do", portfolio_id)
            return CreateResult(self._to_entity(existing), created=False)

        document = {
            "_id": portfolio_id,
            "mode": pid.mode,
            "strategy": strategy,
            # `strategy_key` is "{strategy}_{version}" -- e.g. "momentum_v1" --
            # which is exactly the frozen-version identity signals are keyed by.
            "strategy_version": pid.strategy_key,
            "strategy_params_hash": strategy_params_hash,
            "status": PortfolioStatus.ACTIVE.value,
            "currency": "INR",
            "label": label,
            "initial_capital": initial_capital,
            "monthly_contribution": monthly_contribution,
            "contribution_day": contribution_day,
            # Everything starts as cash; there are no holdings yet, and the
            # first rebalance is what deploys it.
            "cash": initial_capital,
            "total_contributed": initial_capital,
            # Unit accounting starts at the base so a later contribution
            # cannot look like a return.
            "units": _initial_units(initial_capital),
            "execution_mode": execution_mode.value,
            "cost_model": cost_model,
            "account_note": account_note,
            "created_at": now,
            "created_by_commit": code_commit,
        }
        if benchmark_sleeve is not None:
            document["benchmark_sleeve"] = {
                "enabled": benchmark_sleeve.enabled,
                "index": benchmark_sleeve.index,
                "target_weight": benchmark_sleeve.target_weight,
                "value": benchmark_sleeve.value,
                "cost_basis": benchmark_sleeve.cost_basis,
                "last_rebalanced": None,
            }

        try:
            self._repo.insert(document)
        except AlreadyExists:
            # Created concurrently between the read and the insert. The
            # database is the arbiter; re-read and validate rather than retry.
            existing = self._repo.get(portfolio_id)
            self._assert_matches(
                existing, strategy_params_hash=strategy_params_hash,
                initial_capital=initial_capital,
            )
            return CreateResult(self._to_entity(existing), created=False)

        if self._audit:
            self._audit.record(
                now=now, actor="system", action="portfolio.created",
                entity_type="portfolio", entity_id=portfolio_id,
                portfolio_id=portfolio_id,
                after={"initial_capital": str(initial_capital), "label": label},
            )
        log.info("created portfolio %s (%s)", portfolio_id, label)
        return CreateResult(self._to_entity(self._repo.get(portfolio_id)), created=True)

    def _assert_matches(
        self, existing: dict, *, strategy_params_hash: str, initial_capital: Decimal
    ) -> None:
        pid = existing["_id"]
        if existing.get("strategy_params_hash") != strategy_params_hash:
            raise PortfolioConflict(
                f"{pid} was created with strategy params "
                f"{existing.get('strategy_params_hash')!r} but the current config "
                f"hashes to {strategy_params_hash!r}. A parameter change forks a "
                f"NEW portfolio (bump to v2); it never mutates an existing one."
            )
        if existing.get("initial_capital") != initial_capital:
            raise PortfolioConflict(
                f"{pid} exists with initial capital "
                f"{existing.get('initial_capital')}, not {initial_capital}"
            )

    def get(self, portfolio_id: str) -> Portfolio | None:
        doc = self._repo.get(portfolio_id)
        return self._to_entity(doc) if doc else None

    def _to_entity(self, doc: dict) -> Portfolio:
        sleeve = doc.get("benchmark_sleeve")
        return Portfolio(
            portfolio_id=doc["_id"],
            mode=doc["mode"],
            strategy=doc["strategy"],
            strategy_version=doc["strategy_version"],
            strategy_params_hash=doc["strategy_params_hash"],
            status=PortfolioStatus(doc.get("status", "active")),
            label=doc.get("label", ""),
            initial_capital=doc.get("initial_capital") or money.ZERO,
            monthly_contribution=doc.get("monthly_contribution") or money.ZERO,
            contribution_day=doc.get("contribution_day", 1),
            cash=doc.get("cash") or money.ZERO,
            total_contributed=doc.get("total_contributed") or money.ZERO,
            units=doc.get("units") or money.ZERO,
            execution_mode=ExecutionMode(doc.get("execution_mode", "manual")),
            cost_model=doc.get("cost_model", ""),
            account_note=doc.get("account_note", ""),
            benchmark_sleeve=BenchmarkSleeve(
                enabled=sleeve.get("enabled", False),
                index=sleeve.get("index", "NIFTY_50"),
                target_weight=sleeve.get("target_weight", 0.0),
                value=sleeve.get("value") or money.ZERO,
                cost_basis=sleeve.get("cost_basis") or money.ZERO,
            ) if sleeve else None,
            created_by_commit=doc.get("created_by_commit", ""),
        )


def _initial_units(capital: Decimal):
    """Units issued for the opening capital, at the base unit NAV."""
    from algotrade.domain.valuation import BASE_UNIT_NAV, _units

    if capital <= 0:
        return money.ZERO
    return _units(capital / BASE_UNIT_NAV)
