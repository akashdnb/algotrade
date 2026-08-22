"""The `holdings` collection.

Lots are embedded, so a position is read and written atomically -- there is no
window in which a partially-updated lot list could be observed.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.domain import money
from algotrade.domain.models import Holding, Lot
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


def _lot_to_doc(lot: Lot) -> dict[str, Any]:
    return {
        "lot_id": lot.lot_id,
        "acquired_on": codec.to_mongo_date(lot.acquired_on),
        "quantity": lot.quantity,
        "price": codec.to_mongo_money(lot.price),
        "charges": codec.to_mongo_money(lot.charges),
        "source_set_id": lot.source_set_id,
    }


def _lot_from_doc(doc: dict[str, Any]) -> Lot:
    return Lot(
        lot_id=doc["lot_id"],
        acquired_on=codec.from_mongo_date(doc["acquired_on"]),
        quantity=doc["quantity"],
        price=codec.from_mongo_money(doc["price"]),
        charges=codec.from_mongo_money(doc.get("charges")) or money.ZERO,
        source_set_id=doc.get("source_set_id", ""),
    )


class HoldingRepository(Repository):
    collection_name = "holdings"

    @staticmethod
    def key(portfolio_id: str, symbol: str) -> str:
        return f"{portfolio_id}:{symbol}"

    def book(self, portfolio_id: str) -> list[Holding]:
        """Every position, as domain entities."""
        docs = self.collection.find({"portfolio_id": portfolio_id}).sort("symbol", 1)
        return [self._to_entity(d) for d in docs]

    def get_holding(self, portfolio_id: str, symbol: str) -> Holding | None:
        doc = self.collection.find_one({"_id": self.key(portfolio_id, symbol)})
        return self._to_entity(doc) if doc else None

    def save(self, holding: Holding) -> None:
        """Persist, or delete when the position is closed.

        A zero-quantity row is removed rather than kept: leaving it means every
        read must filter it out, and the one place that forgets reports a
        closed position as held.
        """
        _id = self.key(holding.portfolio_id, holding.symbol)
        if holding.is_empty:
            self.collection.delete_one({"_id": _id})
            return

        self.collection.replace_one(
            {"_id": _id},
            {
                "_id": _id,
                "portfolio_id": holding.portfolio_id,
                "symbol": holding.symbol,
                "isin": holding.isin,
                "lots": [_lot_to_doc(lot) for lot in holding.lots],
                "realised_pnl_ytd": codec.to_mongo_money(holding.realised_pnl_ytd),
                "updated_at": codec.to_mongo_instant(holding.updated_at),
            },
            upsert=True,
        )

    def symbols(self, portfolio_id: str) -> list[str]:
        return sorted(
            self.collection.distinct("symbol", {"portfolio_id": portfolio_id})
        )

    def _to_entity(self, doc: dict[str, Any]) -> Holding:
        return Holding(
            portfolio_id=doc["portfolio_id"],
            symbol=doc["symbol"],
            isin=doc.get("isin", ""),
            lots=[_lot_from_doc(l) for l in doc.get("lots", [])],
            realised_pnl_ytd=codec.from_mongo_money(doc.get("realised_pnl_ytd"))
            or money.ZERO,
            updated_at=codec.from_mongo_instant(doc.get("updated_at")),
        )
