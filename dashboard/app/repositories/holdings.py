"""The position book, decoded into the trading system's own domain objects.

This repository deliberately returns `algotrade.domain.models.Holding` rather
than plain dicts. `quantity`, `cost_basis`, `days_held` and `tax_status` are
already defined there -- including the detail that long-term is *strictly*
after 365 days, because the statutory test is "more than 12 months" and
rounding it the generous way understates tax owed.

Re-deriving any of that here would be a second implementation of a rule that
has one correct answer, and the two would drift. Living in the same repository
as the trading system is precisely what makes this reuse available; it is the
main reason the dashboard is not a separate project.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from algotrade.domain.models import Holding, Lot

from app.repositories.base import dec, day, instant


class HoldingRepository:
    def __init__(self, db) -> None:
        self._c = db["holdings"]

    def book(self, portfolio_id: str) -> list[Holding]:
        """Every open position, largest first once priced by the caller."""
        cursor = self._c.find({"portfolio_id": portfolio_id}).sort("symbol", 1)
        return [self._to_holding(doc) for doc in cursor]

    def get(self, portfolio_id: str, symbol: str) -> Holding | None:
        doc = self._c.find_one({"portfolio_id": portfolio_id, "symbol": symbol})
        return self._to_holding(doc) if doc else None

    @staticmethod
    def _to_holding(doc: dict) -> Holding:
        lots = [
            Lot(
                lot_id=raw.get("lot_id", ""),
                acquired_on=day(raw.get("acquired_on")),
                quantity=int(raw.get("quantity", 0)),
                price=dec(raw.get("price")) or Decimal(0),
                charges=dec(raw.get("charges")) or Decimal(0),
                source_set_id=raw.get("source_set_id", ""),
            )
            for raw in doc.get("lots") or []
        ]
        return Holding(
            portfolio_id=doc["portfolio_id"],
            symbol=doc["symbol"],
            isin=doc.get("isin", ""),
            lots=lots,
            realised_pnl_ytd=dec(doc.get("realised_pnl_ytd")) or Decimal(0),
            updated_at=instant(doc.get("updated_at")),
        )


def average_cost(holding: Holding) -> Decimal | None:
    """Weighted average paid per share, charges included.

    None for an empty holding rather than zero: "no position" and "acquired for
    nothing" are different statements and only one of them is ever true.
    """
    quantity = holding.quantity
    if quantity <= 0:
        return None
    total = sum((lot.cost_basis for lot in holding.lots), Decimal(0))
    return total / Decimal(quantity)


def oldest_lot(holding: Holding) -> Lot | None:
    """The lot FIFO would sell first, and therefore the one whose tax status
    applies to the next sale."""
    return min(holding.lots, key=lambda lot: lot.acquired_on, default=None)


def tax_status(holding: Holding, as_of: date) -> str:
    lot = oldest_lot(holding)
    return lot.tax_status(as_of).value if lot else ""


def days_held(holding: Holding, as_of: date) -> int | None:
    lot = oldest_lot(holding)
    return lot.days_held(as_of) if lot else None
