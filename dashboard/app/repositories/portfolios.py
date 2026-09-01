"""The portfolio record: units, contributions, and the frozen strategy hash."""

from __future__ import annotations

from app.repositories.base import decode

MONEY = ("cash", "initial_capital", "monthly_contribution", "total_contributed")
PRECISE = ("units",)

#: Enough to populate the picker without dragging the whole document.
_SUMMARY = {
    "label": 1, "mode": 1, "status": 1, "strategy": 1, "strategy_version": 1,
}


class PortfolioRepository:
    def __init__(self, db) -> None:
        self._c = db["portfolios"]

    def get(self, portfolio_id: str) -> dict | None:
        doc = self._c.find_one({"_id": portfolio_id})
        if not doc:
            return None
        return decode(doc, money=MONEY, precise=PRECISE, instants=("created_at",))

    def list_all(self) -> list[dict]:
        """Every portfolio in this database, active first.

        Portfolios are documents keyed by id, so several strategies and a
        shadow book coexist in one database by design -- it is the environment
        boundary (paper vs prod) that is a separate database, not the
        portfolio boundary.
        """
        docs = list(self._c.find({}, _SUMMARY).sort("_id", 1))
        docs.sort(key=lambda d: (d.get("status") != "active", d["_id"]))
        return docs

    def exists(self, portfolio_id: str) -> bool:
        return self._c.count_documents({"_id": portfolio_id}, limit=1) > 0
