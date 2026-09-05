"""The `accepted_baskets` collection -- what a human approved, at those prices.

The middle of three facts, and they are three collections because they are
three different claims:

    recommendations   what the strategy decided
    accepted_baskets  what I approved, at these prices
    submissions       what was actually sent

**Insert, never upsert.** A basket is a record of a decision, and the point of
storing it is that the thing you execute is the thing you reviewed. Silently
replacing it would let the numbers change under a page someone is reading --
which is precisely what storing it prevents. Accepting twice in a session
raises `AlreadyExists`, and the caller says so rather than overwriting.

`seq` comes from `reconcile.plan`, not the recommendation: the plan orders
sells first and numbers its own instructions. `submission.tag_for` hashes
`(set_id, seq)`, so those numbers become order identity the moment this row is
written -- another reason nothing may quietly renumber them.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class BasketRepository(Repository):
    collection_name = "accepted_baskets"

    @staticmethod
    def key(set_id: str, session: date) -> str:
        return f"{set_id}:{session.isoformat()}"

    def accept(
        self,
        *,
        portfolio_id: str,
        set_id: str,
        session: date,
        lines: Iterable[dict[str, Any]],
        now: datetime,
    ) -> str:
        """Store the approved basket. Raises `AlreadyExists` if one exists.

        `lines` are dicts of seq, action, symbol, quantity, price, and
        optionally estimated_charges and order_type -- the shape
        `services/review.Line` already has.
        """
        rows = [
            {
                "seq": int(line["seq"]),
                "action": str(line["action"]),
                "symbol": line["symbol"],
                "quantity": int(line["quantity"]),
                "price": _money(line["price"]),
                "estimated_charges": _money(line.get("estimated_charges", 0)),
                "order_type": line.get("order_type", "LIMIT"),
            }
            for line in lines
        ]
        _id = self.key(set_id, session)
        self.insert({
            "_id": _id,
            "portfolio_id": portfolio_id,
            "set_id": set_id,
            "session": codec.to_mongo_date(session),
            "accepted_at": codec.to_mongo_instant(now),
            "lines": rows,
        })
        return _id

    def for_session(self, set_id: str, session: date) -> dict | None:
        return self.get(self.key(set_id, session))

    def latest(self, portfolio_id: str) -> dict | None:
        """The most recently accepted basket for this book."""
        found = self.find({"portfolio_id": portfolio_id},
                          sort=[("session", -1)], limit=1)
        return found[0] if found else None

    def line(self, set_id: str, session: date, seq: int) -> dict | None:
        """One instruction, by the seq the tag is derived from.

        Read back rather than recomputed: the caller is about to place an
        order, and it must be the order that was approved, not one priced
        again a few minutes later.
        """
        doc = self.for_session(set_id, session)
        if doc is None:
            return None
        for row in doc.get("lines", ()):
            if int(row.get("seq", 0)) == seq:
                return row
        return None


def _money(value: Any) -> Decimal:
    """Via `str`, never `float` -- these numbers reach an exchange."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))
