"""The `corporate_actions` collection -- append-only, and part of the authority.

`executions` says what was traded. This says what the *registrar* did to those
shares afterwards. Together they are the complete event stream the book is a
fold over, which is why this collection is as authoritative as that one: drop it
and the nightly rebuild reconstructs a pre-split book from post-split prices.

**The ratio is stored as an exact numerator/denominator pair.** A bonus 1:3 is
4/3, and there is no float that is 4/3 -- flooring 1.3333333333333333 during the
fractional-entitlement split silently destroys a share. `Fraction` in, two ints
out, `Fraction` back.

**Refused actions are stored too.** A feed action deliberately not applied is
recorded with its reason, so the reconciliation check can tell "handled and
declined" apart from "never noticed" -- which is exactly how the TDPOWERSYS
split went unseen for three weeks.
"""

from __future__ import annotations

from datetime import date, datetime
from fractions import Fraction
from typing import Any

from algotrade.domain.corporate_actions import CorporateAction
from algotrade.repositories import codec
from algotrade.repositories.base import Repository

APPLIED = "applied"
REFUSED = "refused"


class CorporateActionRepository(Repository):
    collection_name = "corporate_actions"

    @staticmethod
    def key(portfolio_id: str, symbol: str, ex_date: date, kind: str) -> str:
        """`portfolio:symbol:ex_date:kind`.

        Deterministic, so re-running discovery over a window that was already
        processed hits `AlreadyExists` rather than applying a split twice. The
        idempotency guarantee is the index, not an application check (§11.5).

        `kind` is in the key because a bonus and a split can share one ex-date
        and must stay two rows -- that is the BAJFINANCE case from §10.6, where
        collapsing siblings lost a factor of 5.
        """
        return f"{portfolio_id}:{symbol}:{ex_date.isoformat()}:{kind}"

    def record(
        self,
        *,
        portfolio_id: str,
        action: CorporateAction,
        status: str,
        now: datetime,
        isin: str = "",
        refused_reason: str = "",
        implied_ratio: float | None = None,
        deviation: float | None = None,
        quantity_before: int | None = None,
        quantity_after: int | None = None,
    ) -> str:
        """Append one action. Raises `AlreadyExists` if already recorded."""
        return self.insert({
            "_id": self.key(
                portfolio_id, action.symbol, action.ex_date, action.kind
            ),
            "portfolio_id": portfolio_id,
            "symbol": action.symbol,
            "isin": isin,
            "ex_date": codec.to_mongo_date(action.ex_date),
            "ratio_num": action.ratio.numerator,
            "ratio_den": action.ratio.denominator,
            "kind": action.kind,
            "subject": action.subject,
            "status": status,
            "refused_reason": refused_reason,
            "implied_ratio": implied_ratio,
            "deviation": deviation,
            "quantity_before": quantity_before,
            "quantity_after": quantity_after,
            "recorded_at": codec.to_mongo_instant(now),
            # Explicit null rather than absent: `outstanding` queries on it,
            # and in Mongo a missing field and a null one are only the same
            # thing until someone writes a query where they are not.
            "acknowledged_at": None,
            "acknowledged_note": "",
        })

    def applied(self, portfolio_id: str) -> list[CorporateAction]:
        """Every action that changed the book, oldest first.

        Only `applied` rows come back. A refused row is a record of a decision,
        not an event in the stream, so replaying it would apply the very thing
        it documents declining.
        """
        docs = self.collection.find(
            {"portfolio_id": portfolio_id, "status": APPLIED}
        ).sort([("ex_date", 1), ("_id", 1)])
        return [self._to_entity(d) for d in docs]

    def history(self, portfolio_id: str) -> list[dict[str, Any]]:
        """Every row including refusals, for reporting and reconciliation."""
        return [
            self._decode(d)
            for d in self.collection.find({"portfolio_id": portfolio_id}).sort(
                [("ex_date", 1), ("_id", 1)]
            )
        ]

    def seen(self, portfolio_id: str) -> set[str]:
        """Keys already recorded, so discovery can skip them without writing."""
        return {
            d["_id"]
            for d in self.collection.find(
                {"portfolio_id": portfolio_id}, {"_id": 1}
            )
        }

    def outstanding(self, portfolio_id: str) -> list[dict[str, Any]]:
        """Refusals nobody has acknowledged yet.

        These are events the book could not apply -- a demerger, a rights
        issue, a factor the ex-date prices did not corroborate. Each is a
        standing item: the book is wrong until a human reconciles it by hand,
        and no amount of re-running discovery will change that.

        Reported as a WARNING rather than an error, and clearable, because an
        error that cannot be cleared is one you learn to scroll past -- and the
        check it would desensitise you to is the one that catches splits.
        """
        return [
            self._decode(d)
            for d in self.collection.find({
                "portfolio_id": portfolio_id,
                "status": REFUSED,
                "acknowledged_at": None,
            }).sort([("ex_date", 1), ("_id", 1)])
        ]

    def acknowledge(self, action_id: str, *, now: datetime, note: str = "") -> bool:
        """Mark one refusal as dealt with. Returns False if there was no such row.

        Deliberately does NOT touch the book. The system cannot check that a
        demerger was reconciled correctly, so it records only that someone said
        it was, and when -- which is the honest claim.
        """
        result = self.collection.update_one(
            {"_id": action_id, "status": REFUSED},
            {"$set": {
                "acknowledged_at": codec.to_mongo_instant(now),
                "acknowledged_note": note,
            }},
        )
        return result.matched_count == 1

    def _to_entity(self, doc: dict[str, Any]) -> CorporateAction:
        return CorporateAction(
            action_id=doc["_id"],
            symbol=doc["symbol"],
            ex_date=codec.from_mongo_date(doc["ex_date"]),
            ratio=Fraction(int(doc["ratio_num"]), int(doc["ratio_den"])),
            kind=doc.get("kind", ""),
            subject=doc.get("subject", ""),
        )
