"""The `rebalance_runs` collection -- the lock that makes monthly execution
exactly-once, and the record that makes a recommendation reproducible (§9.2)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from algotrade.repositories import codec
from algotrade.repositories.base import AlreadyExists, Repository

IN_FLIGHT = ("pending", "generating", "executing")


class RebalanceRunRepository(Repository):
    collection_name = "rebalance_runs"

    @staticmethod
    def key(portfolio_id: str, period: str) -> str:
        return f"{portfolio_id}:{period}"

    def start(
        self,
        portfolio_id: str,
        period: str,
        *,
        now: datetime,
        as_of_session: date,
        fingerprint: dict[str, Any],
        triggered_by: str = "schedule",
        github_run_id: str | None = None,
    ) -> str:
        """Claim the period. Raises `AlreadyExists` if a run is already recorded.

        The `_id` is `portfolio:period`, so this is enforced by the primary key:
        two concurrent workflow runs cannot both proceed, regardless of timing.
        """
        _id = self.key(portfolio_id, period)
        self.insert({
            "_id": _id,
            "portfolio_id": portfolio_id,
            "period": period,
            "status": "generating",
            "triggered_by": triggered_by,
            "attempt": 1,
            "as_of_session": codec.to_mongo_date(as_of_session),
            "input_fingerprint": fingerprint,
            "started_at": codec.to_mongo_instant(now),
            "github_run_id": github_run_id,
        })
        return _id

    def is_complete(self, portfolio_id: str, period: str) -> bool:
        return self.collection.count_documents(
            {"_id": self.key(portfolio_id, period), "status": "completed"}, limit=1
        ) > 0

    def record_target(
        self, run_id: str, *, target: list[dict], current: list[dict],
        turnover: float,
    ) -> bool:
        """Store the ranked target with feature values.

        This is what lets "why was PERSISTENT bought?" be answered a year later
        with `rank 14, momentum 1.4821` instead of by re-deriving a ranking
        against data that may since have been backfilled (§9.3).
        """
        return self.update(run_id, {
            "target": target, "current": current, "turnover_estimate": turnover,
        })

    def complete(
        self, run_id: str, *, now: datetime, recommendation_set_id: str
    ) -> bool:
        return self.update(run_id, {
            "status": "completed",
            "completed_at": codec.to_mongo_instant(now),
            "recommendation_set_id": recommendation_set_id,
        })

    def fail(self, run_id: str, *, now: datetime, error: dict[str, Any]) -> bool:
        return self.update(run_id, {
            "status": "failed",
            "completed_at": codec.to_mongo_instant(now),
            "error": error,
        })

    def retry(self, run_id: str, *, now: datetime) -> bool:
        """Reopen a failed run in place, incrementing `attempt`.

        The period keeps one document. A second document for the same period
        would defeat the uniqueness this collection exists to provide.
        """
        self.collection.update_one({"_id": run_id}, {"$inc": {"attempt": 1}})
        return self.update(run_id, {
            "status": "generating",
            "started_at": codec.to_mongo_instant(now),
            "error": None,
        })

    def latest(self, portfolio_id: str) -> dict[str, Any] | None:
        return self._decode(
            self.collection.find_one(
                {"portfolio_id": portfolio_id}, sort=[("period", -1)]
            )
        )

    def in_flight(self) -> list[dict[str, Any]]:
        return self.find({"status": {"$in": list(IN_FLIGHT)}})

    def history(self, portfolio_id: str, *, limit: int = 24) -> list[dict[str, Any]]:
        return self.find({"portfolio_id": portfolio_id},
                         sort=[("period", -1)], limit=limit)
