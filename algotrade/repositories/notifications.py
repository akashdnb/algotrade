"""The `notifications` collection -- outbound log and send-once guard.

The `_id` is the dedupe key, so a retried job cannot re-send the same daily
summary. Failure alerts are the deliberate exception: they include a hash of
the failure, because a *different* problem on the same day must still reach
you (§2.8).
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta
from typing import Any, Final

from algotrade.repositories import codec, schema
from algotrade.repositories.base import AlreadyExists, Repository

#: Kinds where a same-day resend is legitimate because the content differs.
DISTINCT_PER_CONTENT: Final[frozenset[str]] = frozenset(
    {"job_failure", "system_health", "stale_data"}
)


def payload_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:12]


class NotificationRepository(Repository):
    collection_name = "notifications"

    @staticmethod
    def key(
        kind: str, portfolio_id: str | None, business_date: date | None,
        *, content_hash: str | None = None,
    ) -> str:
        parts = [kind, portfolio_id or "-",
                 business_date.isoformat() if business_date else "-"]
        if content_hash:
            parts.append(content_hash)
        return ":".join(parts)

    def claim(
        self,
        *,
        kind: str,
        text: str,
        now: datetime,
        portfolio_id: str | None = None,
        business_date: date | None = None,
        chat_id: str = "",
    ) -> str | None:
        """Reserve the right to send. Returns the id, or None if already sent.

        `None` means "a message with this identity has already gone out" -- the
        caller should skip silently rather than treat it as an error. This is
        what stops a re-run of the daily job from sending a second summary.
        """
        digest = payload_hash(text)
        _id = self.key(
            kind, portfolio_id, business_date,
            content_hash=digest if kind in DISTINCT_PER_CONTENT else None,
        )
        try:
            self.insert({
                "_id": _id,
                "kind": kind,
                "portfolio_id": portfolio_id,
                "business_date": codec.to_mongo_date(business_date),
                "channel": "telegram",
                "chat_id": chat_id,
                "payload_hash": digest,
                "rendered_text": text,
                "status": "queued",
                "attempts": 0,
                "expires_at": codec.to_mongo_instant(
                    now + timedelta(days=schema.TTL_DAYS["notifications"])
                ),
            })
        except AlreadyExists:
            return None
        return _id

    def mark_sent(
        self, _id: str, *, now: datetime, telegram_message_id: int | None = None
    ) -> bool:
        self.collection.update_one({"_id": _id}, {"$inc": {"attempts": 1}})
        return self.update(_id, {
            "status": "sent",
            "sent_at": codec.to_mongo_instant(now),
            "telegram_message_id": telegram_message_id,
            "error": None,
        })

    def mark_failed(self, _id: str, *, error: str) -> bool:
        self.collection.update_one({"_id": _id}, {"$inc": {"attempts": 1}})
        return self.update(_id, {"status": "failed", "error": error})

    def failures(self, *, since: date) -> list[dict[str, Any]]:
        """A failed send is invisible by definition -- you cannot be told that
        you cannot be told -- so the watchdog polls this (§10.5)."""
        return self.find({
            "status": "failed",
            "business_date": {"$gte": codec.to_mongo_date(since)},
        }, sort=[("business_date", -1)])

    def pending(self) -> list[dict[str, Any]]:
        return self.find({"status": "queued"})
