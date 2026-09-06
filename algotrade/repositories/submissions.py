"""The `submissions` collection -- what was sent to an exchange, and when.

The row exists *before* the order does. That inversion is the whole design:
a crash then leaves a record saying "we may have submitted this", and the
deterministic tag turns confirming it into a lookup rather than a guess
(design §8).

`_id` is the tag, so a retry computes the same key and collides with its own
earlier attempt instead of creating a second row. `insert` is used rather than
`upsert` for exactly that reason -- a duplicate is the idempotency guarantee
working, not an error.
"""

from __future__ import annotations

from datetime import datetime

from algotrade.repositories import codec
from algotrade.repositories.base import AlreadyExists, Repository
from algotrade.services.submission import INTENT, SUBMITTED, Record


class SubmissionRepository(Repository):
    collection_name = "submissions"

    def get(self, tag: str) -> Record | None:
        doc = super().get(tag)
        if doc is None:
            return None
        return Record(
            tag=doc["_id"],
            state=doc["state"],
            broker_order_id=doc.get("broker_order_id", ""),
            message=doc.get("message", ""),
        )

    def record_intent(
        self,
        *,
        tag: str,
        set_id: str,
        seq: int,
        symbol: str,
        side: str,
        quantity: int,
        now: datetime,
        protocol_day: int | None = None,
    ) -> None:
        """Claim the instruction before touching the broker.

        A duplicate key means a previous attempt already claimed it, which the
        caller has by then decided is safe -- it re-read the row and found it
        at `intent`. Swallowing it here keeps that path from failing on a race
        with itself.
        """
        try:
            self.insert({
                "_id": tag,
                "set_id": set_id,
                "item_seq": seq,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "state": INTENT,
                "broker_order_id": "",
                "protocol_day": protocol_day,
                "message": "",
                "created_at": codec.to_mongo_instant(now),
                "updated_at": codec.to_mongo_instant(now),
            })
        except AlreadyExists:
            return

    def mark_submitted(self, *, tag: str, broker_order_id: str,
                       now: datetime) -> None:
        self.update(tag, {
            "state": SUBMITTED,
            "broker_order_id": broker_order_id,
            "updated_at": codec.to_mongo_instant(now),
        })

    def settle(self, *, tag: str, state: str, now: datetime,
               message: str = "") -> None:
        """Record a terminal outcome once the broker has one.

        Separate from `mark_submitted` because it is a different fact: that
        one says we sent it, this says what became of it.
        """
        self.update(tag, {
            "state": state,
            "message": message,
            "updated_at": codec.to_mongo_instant(now),
        })

    def for_set(self, set_id: str) -> list[dict]:
        return self.find({"set_id": set_id}, sort=[("item_seq", 1)])

    def unsettled(self, set_id: str) -> list[dict]:
        """Rows that have been sent but whose fate is unknown.

        The list a human needs after any interruption: each one is an order
        that may exist at the broker and is not yet reflected in the book.
        """
        return self.find({"set_id": set_id, "state": {"$in": [INTENT, SUBMITTED]}},
                         sort=[("item_seq", 1)])
