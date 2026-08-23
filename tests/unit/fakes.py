"""In-memory repository fakes.

Real MongoDB behaviour that the services depend on is reproduced here --
notably `insert` raising `AlreadyExists` on a duplicate `_id`, which is the
idempotency mechanism the whole scheduling design rests on. A fake that
silently overwrote would let a broken service pass.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from algotrade.repositories.base import AlreadyExists


class FakeRepo:
    """Minimal document store keyed by `_id`."""

    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def insert(self, doc: dict) -> str:
        _id = doc["_id"]
        if _id in self.docs:
            raise AlreadyExists(f"{_id} already exists")
        self.docs[_id] = dict(doc)
        return _id

    def upsert(self, doc: dict) -> bool:
        created = doc["_id"] not in self.docs
        self.docs[doc["_id"]] = dict(doc)
        return created

    def get(self, _id: str) -> dict | None:
        doc = self.docs.get(_id)
        return dict(doc) if doc else None

    def update(self, _id: str, changes: dict) -> bool:
        if _id not in self.docs:
            return False
        self.docs[_id].update(changes)
        return True

    def find(self, query: dict | None = None, **_) -> list[dict]:
        return [dict(d) for d in self.docs.values()]

    def count(self, query: dict | None = None) -> int:
        return len(self.docs)


class FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def record(self, **kwargs) -> None:
        self.entries.append(kwargs)

    def actions(self) -> list[str]:
        return [e["action"] for e in self.entries]


class FakeRebalanceRuns(FakeRepo):
    @staticmethod
    def key(portfolio_id: str, period: str) -> str:
        return f"{portfolio_id}:{period}"

    def is_complete(self, portfolio_id: str, period: str) -> bool:
        doc = self.get(self.key(portfolio_id, period))
        return bool(doc and doc.get("status") == "completed")

    def start(self, portfolio_id, period, *, now, as_of_session, fingerprint,
              triggered_by="schedule", github_run_id=None) -> str:
        _id = self.key(portfolio_id, period)
        self.insert({"_id": _id, "portfolio_id": portfolio_id, "period": period,
                     "status": "generating", "attempt": 1,
                     "triggered_by": triggered_by,
                     "input_fingerprint": fingerprint})
        return _id

    def retry(self, run_id: str, *, now) -> bool:
        doc = self.docs.get(run_id)
        if not doc:
            return False
        doc["attempt"] = doc.get("attempt", 1) + 1
        doc["status"] = "generating"
        return True

    def record_target(self, run_id, *, target, current, turnover) -> bool:
        return self.update(run_id, {"target": target, "current": current,
                                    "turnover_estimate": turnover})

    def complete(self, run_id, *, now, recommendation_set_id) -> bool:
        return self.update(run_id, {"status": "completed",
                                    "recommendation_set_id": recommendation_set_id})

    def fail(self, run_id, *, now, error) -> bool:
        return self.update(run_id, {"status": "failed", "error": error})


class FakeRecommendations(FakeRepo):
    """Stores entities rather than documents, matching the real repo's API."""

    def __init__(self) -> None:
        super().__init__()
        self.sets: dict[str, Any] = {}

    @staticmethod
    def key(portfolio_id: str, period: str, revision: int) -> str:
        return f"{portfolio_id}:{period}:r{revision}"

    def save(self, rec) -> str:
        if rec.set_id in self.sets:
            raise AlreadyExists(rec.set_id)
        self.sets[rec.set_id] = rec
        return rec.set_id

    def get_set(self, set_id: str):
        return self.sets.get(set_id)

    def latest_for_period(self, portfolio_id: str, period: str):
        candidates = [
            r for r in self.sets.values()
            if r.portfolio_id == portfolio_id and r.period == period
        ]
        return max(candidates, key=lambda r: r.revision) if candidates else None

    def next_revision(self, portfolio_id: str, period: str) -> int:
        latest = self.latest_for_period(portfolio_id, period)
        return latest.revision + 1 if latest else 1

    def mark_superseded(self, set_id: str) -> bool:
        from algotrade.domain.models import SetStatus

        if set_id not in self.sets:
            return False
        self.sets[set_id].status = SetStatus.SUPERSEDED
        return True

    def update(self, set_id: str, changes: dict) -> bool:
        from algotrade.domain.models import SetStatus

        if set_id not in self.sets:
            return False
        if "status" in changes:
            self.sets[set_id].status = SetStatus(changes["status"])
        return True

    def expiring(self, *, as_of: date) -> list[dict]:
        from algotrade.domain.models import SetStatus

        live = {SetStatus.PENDING, SetStatus.NOTIFIED, SetStatus.PARTIALLY_EXECUTED}
        return [
            {"_id": r.set_id, "portfolio_id": r.portfolio_id,
             "status": r.status.value}
            for r in self.sets.values()
            if r.status in live and r.expires_on and r.expires_on < as_of
        ]
