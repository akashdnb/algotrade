"""Fills: what actually happened, against what was instructed.

The Indian financial year runs **1 April to 31 March**, which is the window
every tax question is asked in. It is defined here because the trading system
has no notion of it -- `realised_pnl_ytd` accumulates without reference to one.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.repositories.base import Page, decode

MONEY = ("price", "charges", "reference_price")

FY_START_MONTH = 4


def financial_year(day: date) -> int:
    """The FY a date falls in, named by its STARTING year.

    2026-08-28 and 2027-02-14 are both FY 2026 ("2026-27"); 2027-04-01 begins
    FY 2027.
    """
    return day.year if day.month >= FY_START_MONTH else day.year - 1


def fy_bounds(year: int) -> tuple[date, date]:
    """[start, end) — the end is EXCLUSIVE, i.e. the next 1 April.

    Half-open rather than ending on 31 March, so no fill can fall between two
    financial years and vanish from both exports.
    """
    return date(year, FY_START_MONTH, 1), date(year + 1, FY_START_MONTH, 1)


def fy_label(year: int) -> str:
    return f"{year}-{str(year + 1)[-2:]}"


class ExecutionRepository:
    def __init__(self, db) -> None:
        self._c = db["executions"]

    def _query(
        self, portfolio_id: str, *, fy: int | None = None,
        symbol: str = "", side: str = "",
    ) -> dict:
        query: dict = {"portfolio_id": portfolio_id}
        if fy is not None:
            start, end = fy_bounds(fy)
            # `executed_on` is a plain YYYY-MM-DD string, so a lexical range is
            # also a chronological one.
            query["executed_on"] = {"$gte": start.isoformat(), "$lt": end.isoformat()}
        if symbol:
            query["symbol"] = symbol.upper()
        if side in ("buy", "sell"):
            query["action"] = side
        return query

    def page(
        self, portfolio_id: str, *, limit: int = 50, offset: int = 0,
        fy: int | None = None, symbol: str = "", side: str = "",
    ) -> Page:
        query = self._query(portfolio_id, fy=fy, symbol=symbol, side=side)
        total = self._c.count_documents(query)
        cursor = (self._c.find(query)
                  .sort([("executed_on", -1), ("_id", -1)])
                  .skip(offset).limit(limit))
        return Page([self._decode(d) for d in cursor], total, limit, offset)

    def all_for_fy(
        self, portfolio_id: str, *, fy: int, symbol: str = "", side: str = "",
    ) -> list[dict]:
        """Unpaginated, for the CSV. A tax export that silently stopped at
        page one would be worse than no export."""
        query = self._query(portfolio_id, fy=fy, symbol=symbol, side=side)
        cursor = self._c.find(query).sort([("executed_on", 1), ("_id", 1)])
        return [self._decode(d) for d in cursor]

    def financial_years(self, portfolio_id: str) -> list[int]:
        """Every FY with at least one fill, newest first."""
        days = self._c.distinct("executed_on", {"portfolio_id": portfolio_id})
        years = set()
        for value in days:
            if not value:
                continue
            try:
                years.add(financial_year(date.fromisoformat(str(value))))
            except ValueError:
                continue
        return sorted(years, reverse=True)

    def totals(self, rows: list[dict]) -> dict:
        buys = [r for r in rows if r.get("action") == "buy"]
        sells = [r for r in rows if r.get("action") == "sell"]

        def value(items):
            return sum(((r.get("price") or Decimal(0)) * (r.get("quantity") or 0)
                        for r in items), Decimal(0))

        return {
            "count": len(rows),
            "buy_value": value(buys),
            "sell_value": value(sells),
            "charges": sum((r.get("charges") or Decimal(0) for r in rows), Decimal(0)),
        }

    @staticmethod
    def _decode(doc: dict) -> dict:
        return decode(doc, money=MONEY, dates=("executed_on",),
                      instants=("executed_at",))
