"""The audit trail, paginated by cursor rather than offset.

`audit_logs` is the one collection that grows without bound -- roughly 15k rows
a year. `skip(n)` degrades linearly with depth, and worse, it can silently skip
or repeat a row when new entries arrive while you are paging. Keyset pagination
on `ts` is constant-time at any depth and cannot do either.
"""

from __future__ import annotations

import re
from datetime import datetime

from app.repositories.base import decode

#: Actions share a `noun.verb` shape, so a prefix filter narrows to one
#: subsystem: `execution.` for everything that touched a fill.
PREFIXES = ("portfolio.", "recommendation.", "execution.", "holdings.",
            "rebalance.", "signal.", "cash.")


class AuditRepository:
    def __init__(self, db) -> None:
        self._c = db["audit_logs"]

    def page(
        self,
        *,
        portfolio_id: str | None = None,
        action: str = "",
        entity_id: str = "",
        severity: str = "",
        before: datetime | None = None,
        limit: int = 50,
    ) -> tuple[list[dict], datetime | None]:
        """Returns (rows, next_cursor). `next_cursor` is None on the last page.

        One extra row is fetched to decide whether a next page exists, then
        discarded -- cheaper and more reliable than counting a growing
        collection.
        """
        query: dict = {}
        if portfolio_id:
            query["portfolio_id"] = portfolio_id
        if entity_id:
            query["entity_id"] = entity_id
        if severity:
            query["severity"] = severity
        if action:
            # Anchored and escaped: an unescaped user string here would be a
            # regex injection, and an unanchored one cannot use an index.
            query["action"] = {"$regex": f"^{re.escape(action)}"}
        if before is not None:
            query["ts"] = {"$lt": before}

        rows = [
            decode(d, instants=("ts",))
            for d in self._c.find(query).sort("ts", -1).limit(limit + 1)
        ]
        next_cursor = rows[limit]["ts"] if len(rows) > limit else None
        return rows[:limit], next_cursor

    def actions(self, portfolio_id: str | None = None) -> list[str]:
        """Distinct action prefixes actually present, for the filter."""
        query = {"portfolio_id": portfolio_id} if portfolio_id else {}
        seen = set()
        for value in self._c.distinct("action", query):
            if value and "." in value:
                seen.add(value.split(".", 1)[0] + ".")
        return sorted(seen)
