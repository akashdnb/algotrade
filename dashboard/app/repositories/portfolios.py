"""The portfolio record: units, contributions, and the frozen strategy hash."""

from __future__ import annotations

from app.repositories.base import decode

MONEY = ("cash", "initial_capital", "monthly_contribution", "total_contributed")
PRECISE = ("units",)


class PortfolioRepository:
    def __init__(self, db) -> None:
        self._c = db["portfolios"]

    def get(self, portfolio_id: str) -> dict | None:
        doc = self._c.find_one({"_id": portfolio_id})
        if not doc:
            return None
        return decode(doc, money=MONEY, precise=PRECISE,
                      instants=("created_at",))
