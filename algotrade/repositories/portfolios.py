"""The `portfolios` collection."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from algotrade.repositories.base import Repository


class PortfolioRepository(Repository):
    collection_name = "portfolios"

    def active(self, *, mode: str | None = None) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"status": "active"}
        if mode:
            query["mode"] = mode
        return self.find(query, sort=[("_id", 1)])

    def active_ids(self, *, mode: str | None = None) -> list[str]:
        """Just the ids -- what the GitHub Actions matrix consumes (§4.2)."""
        return [p["_id"] for p in self.active(mode=mode)]

    def set_cash(self, portfolio_id: str, cash: Decimal) -> bool:
        return self.update(portfolio_id, {"cash": cash})

    def apply_contribution(
        self, portfolio_id: str, *, cash: Decimal, units: Decimal,
        total_contributed: Decimal,
    ) -> bool:
        return self.update(portfolio_id, {
            "cash": cash, "units": units, "total_contributed": total_contributed,
        })

    def params_hash(self, portfolio_id: str) -> str | None:
        """The frozen-strategy hash this portfolio was created with (§1.3).

        Compared against the live config before every rebalance; a mismatch
        stops the run rather than silently trading a changed strategy.
        """
        doc = self.collection.find_one(
            {"_id": portfolio_id}, {"strategy_params_hash": 1}
        )
        return doc.get("strategy_params_hash") if doc else None
