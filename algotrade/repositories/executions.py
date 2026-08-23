"""The `executions` collection -- append-only, the authoritative trade record.

`recommendations.items` holds the *instruction* and a display rollup; this holds
what actually happened. Where the two disagree, this wins, and
`domain.models.reconcile_item_rollup` exists to find that disagreement.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from algotrade.domain import money
from algotrade.domain.models import Action, Execution
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class ExecutionRepository(Repository):
    collection_name = "executions"

    @staticmethod
    def key(set_id: str, item_seq: int, ordinal: int) -> str:
        """`set:seq:n` -- n allows several fills against one instruction."""
        return f"{set_id}:{item_seq}:{ordinal}"

    def next_ordinal(self, set_id: str, item_seq: int) -> int:
        return self.collection.count_documents(
            {"set_id": set_id, "item_seq": item_seq}
        ) + 1

    def record(self, execution: Execution, *, business_date: date) -> str:
        """Append one fill.

        Raises `AlreadyExists` on a duplicate `_id`, and the unique index on
        `broker_order_id` catches the realistic manual-entry mistake: pasting
        the same fill twice.
        """
        return self.insert({
            "_id": execution.execution_id,
            "portfolio_id": execution.portfolio_id,
            "set_id": execution.set_id,
            "item_seq": execution.item_seq,
            "symbol": execution.symbol,
            "isin": execution.isin,
            "action": execution.action.value,
            "quantity": execution.quantity,
            "price": execution.price,
            "charges": execution.charges,
            "reference_price": execution.reference_price,
            "executed_at": codec.to_mongo_instant(execution.executed_at),
            "executed_on": codec.to_mongo_date(business_date),
            "broker_order_id": execution.broker_order_id,
            "slippage_bps": execution.slippage_bps,
        })

    def has_order_id(self, portfolio_id: str, broker_order_id: str) -> bool:
        """Whether this broker order is already recorded.

        Checked BEFORE the over-fill validation, so re-submitting the same
        command reads as "already recorded" rather than "this would exceed the
        instruction" -- which is the confusing message the realistic mistake
        would otherwise produce.
        """
        if not broker_order_id:
            return False
        return self.collection.count_documents(
            {"portfolio_id": portfolio_id, "broker_order_id": broker_order_id},
            limit=1,
        ) > 0

    def for_set(self, set_id: str) -> list[Execution]:
        docs = self.collection.find({"set_id": set_id}).sort(
            [("item_seq", 1), ("executed_at", 1)]
        )
        return [self._to_entity(d) for d in docs]

    def for_item(self, set_id: str, item_seq: int) -> list[Execution]:
        docs = self.collection.find(
            {"set_id": set_id, "item_seq": item_seq}
        ).sort("executed_at", 1)
        return [self._to_entity(d) for d in docs]

    def between(
        self, portfolio_id: str, *, start: date, end: date,
        action: str | None = None,
    ) -> list[Execution]:
        """Every fill in a date range -- the tax-year query (§2.3).

        A `find` rather than an `$unwind` over recommendation sets, which is the
        whole reason this collection is separate.
        """
        query: dict[str, Any] = {
            "portfolio_id": portfolio_id,
            "executed_on": {
                "$gte": codec.to_mongo_date(start),
                "$lte": codec.to_mongo_date(end),
            },
        }
        if action:
            query["action"] = action
        docs = self.collection.find(query).sort("executed_on", 1)
        return [self._to_entity(d) for d in docs]

    def realised_slippage_bps(
        self, portfolio_id: str, *, start: date, end: date
    ) -> float | None:
        """Value-weighted realised slippage, the §8.4 dashboard number.

        Answers §11o's open evidence item (a): are real fills inside the
        modelled 30 bps round trip?
        """
        fills = [
            e for e in self.between(portfolio_id, start=start, end=end)
            if e.slippage_bps is not None
        ]
        total = money.sum_rupees(e.value for e in fills)
        if not fills or total == 0:
            return None
        return sum(float(e.value) * e.slippage_bps for e in fills) / float(total)

    def _to_entity(self, doc: dict[str, Any]) -> Execution:
        m = codec.from_mongo_money
        return Execution(
            execution_id=doc["_id"],
            portfolio_id=doc["portfolio_id"],
            set_id=doc["set_id"],
            item_seq=doc["item_seq"],
            symbol=doc["symbol"],
            isin=doc.get("isin", ""),
            action=Action(doc["action"]),
            quantity=doc["quantity"],
            price=m(doc["price"]),
            charges=m(doc.get("charges")) or money.ZERO,
            reference_price=m(doc.get("reference_price")) or money.ZERO,
            executed_at=codec.from_mongo_instant(doc["executed_at"]),
            broker_order_id=doc.get("broker_order_id", ""),
        )
