"""The `cash_transactions` collection -- the cash ledger.

`portfolios.cash` is a **cached** balance. This is the authority. A mutable
running total cannot explain how it reached its value and cannot detect that it
drifted; a ledger can do both, and `reconcile` asserts the two agree.

Every entry carries a **signed** amount -- money in positive, money out
negative -- so the balance is `sum(amount)` and there is no second column to
disagree with the first.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from algotrade.domain import money
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class CashRepository(Repository):
    collection_name = "cash_transactions"

    @staticmethod
    def key(portfolio_id: str, kind: str, discriminator: str) -> str:
        """Deterministic, so replaying the same event cannot double-count.

        `discriminator` is the execution id for a fill, the business date for a
        contribution, and so on -- whatever makes the event unique.
        """
        return f"{portfolio_id}:{kind}:{discriminator}"

    def record(
        self,
        *,
        portfolio_id: str,
        kind: str,
        amount: Decimal,
        on: date,
        now: datetime,
        discriminator: str,
        symbol: str | None = None,
        execution_id: str | None = None,
        set_id: str | None = None,
        note: str = "",
    ) -> str:
        """Append one entry. Raises `AlreadyExists` if it is already recorded."""
        _id = self.key(portfolio_id, kind, discriminator)
        self.insert({
            "_id": _id,
            "portfolio_id": portfolio_id,
            "date": codec.to_mongo_date(on),
            "kind": kind,
            "amount": amount,
            "symbol": symbol,
            "execution_id": execution_id,
            "set_id": set_id,
            "note": note,
            "created_at": codec.to_mongo_instant(now),
        })
        return _id

    def balance(self, portfolio_id: str, *, through: date | None = None) -> Decimal:
        """Cash as of `through`. Summed in the database, not in Python.

        `$sum` on Decimal128 is exact -- the aggregation pipeline does not
        convert to double -- so this cannot drift from a client-side sum.
        """
        match: dict[str, Any] = {"portfolio_id": portfolio_id}
        if through is not None:
            match["date"] = {"$lte": codec.to_mongo_date(through)}
        result = list(self.collection.aggregate([
            {"$match": match},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]))
        if not result:
            return money.ZERO
        return codec.from_mongo_money(result[0]["total"]) or money.ZERO

    def entries(
        self, portfolio_id: str, *, start: date | None = None,
        end: date | None = None, kind: str | None = None,
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"portfolio_id": portfolio_id}
        if start or end:
            bounds: dict[str, str] = {}
            if start:
                bounds["$gte"] = codec.to_mongo_date(start)
            if end:
                bounds["$lte"] = codec.to_mongo_date(end)
            query["date"] = bounds
        if kind:
            query["kind"] = kind
        return self.find(query, sort=[("date", 1), ("_id", 1)])

    def total_by_kind(self, portfolio_id: str) -> dict[str, Decimal]:
        """Where the money went, for the reconciliation report."""
        rows = self.collection.aggregate([
            {"$match": {"portfolio_id": portfolio_id}},
            {"$group": {"_id": "$kind", "total": {"$sum": "$amount"}}},
        ])
        return {
            r["_id"]: codec.from_mongo_money(r["total"]) or money.ZERO
            for r in rows
        }

    def contributions(self, portfolio_id: str) -> list[tuple[date, Decimal]]:
        """(date, amount) for every external cash movement.

        The XIRR flow list: contributions and withdrawals only. Buys and sells
        move money *within* the portfolio and are not investor cash flows --
        including them would compute the return of the broker, not of you.
        """
        return [
            (codec.from_mongo_date(e["date"]), e["amount"])
            for e in self.entries(portfolio_id)
            if e["kind"] in ("opening_balance", "contribution", "withdrawal")
        ]

    def execution_ids(self, portfolio_id: str) -> set[str]:
        """Which executions already have a ledger entry, for reconciliation."""
        return {
            e["execution_id"]
            for e in self.find({"portfolio_id": portfolio_id,
                                "execution_id": {"$type": "string"}})
            if e.get("execution_id")
        }
