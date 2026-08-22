"""The `audit_logs` collection -- append-only.

Retention is split deliberately. Financial actions never expire; operational
noise carries an `expires_at` and is swept at 90 days. The split is by
*action prefix*, so adding a new financial action does not require remembering
to exempt it -- only the explicitly-operational prefixes get a TTL.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from algotrade.repositories import codec, schema
from algotrade.repositories.base import Repository

#: Everything else is treated as a financial record and kept forever.
OPERATIONAL_PREFIXES: Final[tuple[str, ...]] = ("job.", "data.", "notification.")


def is_operational(action: str) -> bool:
    return action.startswith(OPERATIONAL_PREFIXES)


class AuditRepository(Repository):
    collection_name = "audit_logs"

    def record(
        self,
        *,
        now: datetime,
        actor: str,
        action: str,
        entity_type: str,
        entity_id: str,
        portfolio_id: str | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Append one entry. Never raises on a duplicate -- there is no natural
        key, because the same action legitimately recurs."""
        doc: dict[str, Any] = {
            "ts": codec.to_mongo_instant(now),
            "portfolio_id": portfolio_id,
            "actor": actor,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "severity": severity,
        }
        if before is not None:
            doc["before"] = before
        if after is not None:
            doc["after"] = after
        if context is not None:
            doc["context"] = context

        # Only operational INFO expires. A warning or error is worth keeping
        # even about a job, because it is the evidence for a later diagnosis.
        if severity == "info" and is_operational(action):
            doc["expires_at"] = codec.to_mongo_instant(
                now + timedelta(days=schema.TTL_DAYS["audit_logs_operational"])
            )

        self.collection.insert_one(doc)

    def for_entity(self, entity_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        """The full history of one entity -- the recommendation trace (§9.3)."""
        return self.find({"entity_id": entity_id}, sort=[("ts", 1)], limit=limit)

    def for_portfolio(
        self, portfolio_id: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        query: dict[str, Any] = {"portfolio_id": portfolio_id}
        if since:
            query["ts"] = {"$gte": codec.to_mongo_instant(since)}
        return self.find(query, sort=[("ts", -1)], limit=limit)

    def problems(self, *, since: datetime, limit: int = 50) -> list[dict[str, Any]]:
        return self.find(
            {"severity": {"$in": ["warning", "error"]},
             "ts": {"$gte": codec.to_mongo_instant(since)}},
            sort=[("ts", -1)], limit=limit,
        )
