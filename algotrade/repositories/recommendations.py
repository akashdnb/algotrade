"""The `recommendations` collection -- the trade record.

Sets are **immutable in identity**: a bad set is superseded by a new revision,
never edited (§11.4). Item *status* does change, because recording a fill is
what the collection is for -- but the instruction itself (symbol, quantity,
reference price) is written once.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.domain import money
from algotrade.domain.models import (
    Action,
    Item,
    ItemStatus,
    RecommendationSet,
    SetStatus,
)
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


def _item_to_doc(item: Item) -> dict[str, Any]:
    return {
        "seq": item.seq,
        "action": item.action.value,
        "symbol": item.symbol,
        "isin": item.isin,
        "reason": item.reason,
        "quantity": item.quantity,
        "reference_price": codec.to_mongo_money(item.reference_price),
        "estimated_charges": codec.to_mongo_money(item.estimated_charges),
        "status": item.status.value,
        "executed_quantity": item.executed_quantity,
        "executed_price": codec.to_mongo_money(item.executed_price),
        "executed_charges": codec.to_mongo_money(item.executed_charges),
        "executed_at": codec.to_mongo_instant(item.executed_at),
        "broker_order_id": item.broker_order_id,
        "skip_reason": item.skip_reason,
        # Denormalised so the API and dashboard need no computation, and so a
        # stored value can be diffed against a recomputed one during an audit.
        "slippage_bps": item.slippage_bps,
    }


def _item_from_doc(doc: dict[str, Any]) -> Item:
    m = codec.from_mongo_money
    return Item(
        seq=doc["seq"],
        action=Action(doc["action"]),
        symbol=doc["symbol"],
        isin=doc.get("isin", ""),
        reason=doc.get("reason", ""),
        quantity=doc["quantity"],
        reference_price=m(doc["reference_price"]),
        estimated_charges=m(doc.get("estimated_charges")) or money.ZERO,
        status=ItemStatus(doc["status"]),
        executed_quantity=doc.get("executed_quantity", 0),
        executed_price=m(doc.get("executed_price")),
        executed_charges=m(doc.get("executed_charges")) or money.ZERO,
        executed_at=codec.from_mongo_instant(doc.get("executed_at")),
        broker_order_id=doc.get("broker_order_id", ""),
        skip_reason=doc.get("skip_reason", ""),
    )


class RecommendationRepository(Repository):
    collection_name = "recommendations"

    @staticmethod
    def key(portfolio_id: str, period: str, revision: int) -> str:
        return f"{portfolio_id}:{period}:r{revision}"

    def save(self, rec: RecommendationSet) -> str:
        """Insert a new set. Raises `AlreadyExists` if this revision is stored.

        Insert, never upsert: overwriting a set that may already have recorded
        fills would destroy the trade record.
        """
        return self.insert({
            "_id": rec.set_id,
            "portfolio_id": rec.portfolio_id,
            "rebalance_run_id": rec.rebalance_run_id,
            "period": rec.period,
            "revision": rec.revision,
            "supersedes": rec.supersedes,
            "status": rec.status.value,
            "generated_at": codec.to_mongo_instant(rec.generated_at),
            "as_of_session": codec.to_mongo_date(rec.as_of_session),
            "expires_on": codec.to_mongo_date(rec.expires_on),
            "fill_convention": rec.fill_convention,
            "reference_nav": rec.reference_nav,
            "reference_cash": rec.reference_cash,
            "estimated_total_charges": codec.to_mongo_money(
                rec.estimated_total_charges
            ),
            # Computed by the rebalance and printed by the job, but never
            # stored until 2026-09-05 -- so `holds` read back empty, every
            # alert said "Holds 0", and the record of what the strategy chose
            # to KEEP in a given month did not exist. The trades were always
            # right; the decision was only half recorded.
            "holds": list(rec.holds),
            "items": [_item_to_doc(i) for i in rec.items],
            "totals": rec.counts(),
            "realised_slippage_bps": rec.realised_slippage_bps(),
            "code_commit": rec.code_commit,
            "strategy_params_hash": rec.strategy_params_hash,
        })

    def get_set(self, set_id: str) -> RecommendationSet | None:
        doc = self.collection.find_one({"_id": set_id})
        return self._to_entity(doc) if doc else None

    def get_many(self, set_ids: list[str]) -> dict[str, RecommendationSet]:
        """Several sets in ONE round trip, keyed by id.

        A caller showing a page of rebalance periods needs one set per row.
        Calling `get_set` in that loop is N round trips, and on a serverless
        host with a single-connection pool they are strictly sequential.
        """
        if not set_ids:
            return {}
        docs = self.collection.find({"_id": {"$in": list(set_ids)}})
        return {d["_id"]: self._to_entity(self._decode(d)) for d in docs}

    def latest_for_period(
        self, portfolio_id: str, period: str
    ) -> RecommendationSet | None:
        doc = self.collection.find_one(
            {"portfolio_id": portfolio_id, "period": period},
            sort=[("revision", -1)],
        )
        return self._to_entity(doc) if doc else None

    def next_revision(self, portfolio_id: str, period: str) -> int:
        latest = self.collection.find_one(
            {"portfolio_id": portfolio_id, "period": period},
            sort=[("revision", -1)], projection={"revision": 1},
        )
        return (latest["revision"] + 1) if latest else 1

    def update_items(self, rec: RecommendationSet) -> bool:
        """Rewrite the item array and the derived summary after fills.

        The set's identity and instructions are unchanged; only execution state
        and the derived rollups move.
        """
        return self.update(rec.set_id, {
            "items": [_item_to_doc(i) for i in rec.items],
            "status": rec.derive_status().value,
            "totals": rec.counts(),
            "realised_slippage_bps": rec.realised_slippage_bps(),
        })

    def mark_superseded(self, set_id: str) -> bool:
        return self.update(set_id, {"status": SetStatus.SUPERSEDED.value})

    def outstanding(self, portfolio_id: str | None = None) -> list[dict[str, Any]]:
        """Sets with work still to do -- what the watchdog counts (§10.1)."""
        query: dict[str, Any] = {
            "status": {"$in": [SetStatus.PENDING.value, SetStatus.NOTIFIED.value,
                               SetStatus.PARTIALLY_EXECUTED.value]}
        }
        if portfolio_id:
            query["portfolio_id"] = portfolio_id
        return self.find(query, sort=[("as_of_session", 1)])

    def expiring(self, *, as_of: date) -> list[dict[str, Any]]:
        return self.find({
            "status": {"$in": [SetStatus.PENDING.value, SetStatus.NOTIFIED.value,
                               SetStatus.PARTIALLY_EXECUTED.value]},
            "expires_on": {"$lt": codec.to_mongo_date(as_of)},
        })

    def history(self, portfolio_id: str, *, limit: int = 24) -> list[dict[str, Any]]:
        return self.find({"portfolio_id": portfolio_id},
                         sort=[("period", -1), ("revision", -1)], limit=limit)

    def _to_entity(self, doc: dict[str, Any]) -> RecommendationSet:
        m = codec.from_mongo_money
        return RecommendationSet(
            set_id=doc["_id"],
            portfolio_id=doc["portfolio_id"],
            rebalance_run_id=doc.get("rebalance_run_id", ""),
            period=doc["period"],
            revision=doc.get("revision", 1),
            supersedes=doc.get("supersedes"),
            status=SetStatus(doc["status"]),
            generated_at=codec.from_mongo_instant(doc.get("generated_at")),
            as_of_session=codec.from_mongo_date(doc["as_of_session"]),
            expires_on=codec.from_mongo_date(doc.get("expires_on")),
            fill_convention=doc.get("fill_convention", "next_open"),
            reference_nav=m(doc.get("reference_nav")) or money.ZERO,
            reference_cash=m(doc.get("reference_cash")) or money.ZERO,
            holds=list(doc.get("holds", [])),
            items=[_item_from_doc(i) for i in doc.get("items", [])],
            code_commit=doc.get("code_commit", ""),
            strategy_params_hash=doc.get("strategy_params_hash", ""),
        )
