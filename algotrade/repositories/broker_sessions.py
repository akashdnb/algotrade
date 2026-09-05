"""The `broker_sessions` collection -- one encrypted access token at a time.

Stores ciphertext only. The key lives in `BROKER_TOKEN_KEY`, outside the
database, so a leaked dump yields nothing usable (design §5).

**The TTL index is a cleaner, not a clock.** Mongo's TTL monitor runs roughly
once a minute, so a row can outlive its `expires_at` by up to that long. Every
read therefore checks expiry itself, through `tokens.is_usable` -- one function,
so the repository, the view and any future job cannot disagree about whether a
token is alive.

The stored `expires_at` is itself held a `tokens.SAFETY_MARGIN` before Kite's
own 06:00, which covers what the read-check cannot: our clock is not their
clock, and a session that passes the check at the start of an action can die
during it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from algotrade.brokers import tokens
from algotrade.repositories import codec
from algotrade.repositories.base import Repository


class BrokerSessionRepository(Repository):
    collection_name = "broker_sessions"

    def store(
        self,
        *,
        broker: str,
        ciphertext: str,
        expires_at: datetime,
        now: datetime,
    ) -> None:
        """Replace any existing session for this broker.

        `upsert` rather than `insert`: re-authorising mid-day is a normal thing
        to do, and the previous token is dead the moment a new one is issued.
        Nothing is lost, because a session is not a record of a decision.
        """
        self.upsert({
            "_id": broker,
            "broker": broker,
            "ciphertext": ciphertext,
            "expires_at": codec.to_mongo_instant(expires_at),
            "created_at": codec.to_mongo_instant(now),
        })

    def live(self, broker: str, *, now: datetime) -> str | None:
        """The ciphertext of a token that has not expired, or None.

        Returns None rather than raising: "no live session" is the ordinary
        state for most of the day, and the caller's response is to ask the user
        to authorise, not to fail.
        """
        doc = self.get(broker)
        if doc is None:
            return None
        expires_at = codec.from_mongo_instant(doc.get("expires_at"))
        if not tokens.is_usable(expires_at, now):
            return None
        return doc.get("ciphertext") or None

    def expires_at(self, broker: str) -> datetime | None:
        """When the stored session dies. For display, so a human can see it."""
        doc: dict[str, Any] | None = self.get(broker)
        if doc is None:
            return None
        return codec.from_mongo_instant(doc.get("expires_at"))

    def drop(self, broker: str) -> bool:
        """Forget the session locally.

        The caller must invalidate it at the broker as well -- deleting only
        this row leaves the token alive on their side, which is the opposite of
        what "log out" is understood to mean.
        """
        return self.delete(broker)
