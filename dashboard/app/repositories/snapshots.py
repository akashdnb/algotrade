"""Portfolio snapshots: NAV, unit accounting, drawdown, and the position book."""

from __future__ import annotations

from datetime import date

from app.repositories.base import Page, decode

MONEY = (
    "nav", "cash", "invested_value", "market_value", "invested_capital",
    "realised_pnl", "unrealised_pnl", "total_pnl", "total_contributed",
    "benchmark_close", "benchmark_sleeve_value",
)
#: NOT money. `units` carries 3 decimals and `unit_nav` 4; rounding either to
#: paise silently distorts every return computed from them.
PRECISE = ("units", "unit_nav", "peak_unit_nav")

POSITION_MONEY = ("close", "value", "unrealised_pnl")

#: Everything except the positions array, which is ~90% of a snapshot's bytes
#: and is never needed for a chart or a list.
_SUMMARY_FIELDS = {
    "date": 1, "nav": 1, "unit_nav": 1, "drawdown": 1, "day_return": 1,
    "total_return": 1, "cash": 1, "benchmark_close": 1, "price_date": 1,
}


class SnapshotRepository:
    def __init__(self, db) -> None:
        self._c = db["portfolio_snapshots"]

    def latest(self, portfolio_id: str) -> dict | None:
        doc = self._c.find_one({"portfolio_id": portfolio_id}, sort=[("date", -1)])
        return self._decode(doc) if doc else None

    def on(self, portfolio_id: str, when: date) -> dict | None:
        doc = self._c.find_one(
            {"portfolio_id": portfolio_id, "date": when.isoformat()}
        )
        return self._decode(doc) if doc else None

    def series(self, portfolio_id: str, *, since: date | None = None) -> list[dict]:
        """NAV history for charting, without the positions array.

        Projecting positions away turns a ~300 KB response into ~20 KB, which
        matters inside a serverless function's time budget.
        """
        query: dict = {"portfolio_id": portfolio_id}
        if since is not None:
            query["date"] = {"$gte": since.isoformat()}
        cursor = self._c.find(query, _SUMMARY_FIELDS).sort("date", 1)
        return [self._decode(d) for d in cursor]

    def page(self, portfolio_id: str, *, limit: int, offset: int) -> Page:
        query = {"portfolio_id": portfolio_id}
        total = self._c.count_documents(query)
        cursor = (
            self._c.find(query, {"positions": 0})
            .sort("date", -1).skip(offset).limit(limit)
        )
        return Page([self._decode(d) for d in cursor], total, limit, offset)

    def count(self, portfolio_id: str) -> int:
        return self._c.count_documents({"portfolio_id": portfolio_id})

    @staticmethod
    def _decode(doc: dict) -> dict:
        out = decode(doc, money=MONEY, precise=PRECISE,
                     dates=("date", "price_date"), instants=("computed_at",))
        if "positions" in doc:
            out["positions"] = [
                decode(p, money=POSITION_MONEY) for p in doc.get("positions") or []
            ]
        return out
