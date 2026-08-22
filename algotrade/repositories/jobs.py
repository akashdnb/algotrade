"""The `job_runs` collection -- the reconciliation ledger.

Two things live here that the whole scheduling design rests on:

* **`claim`/`complete`** turn "did the cron fire?" into "is there a successful
  run for this business date?" The unique partial index on
  `(job_name, business_date) where status = "success"` means a second success
  cannot be recorded, so a duplicated workflow run is stopped by the database
  rather than by an application check that could race (§11.5).
* **`missing_dates`** is the gap query every job uses to decide its work.
  A missed Tuesday is repaired by Wednesday's run because Wednesday asks this
  question, not because anything remembered Tuesday failed (§0.1).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from algotrade.platform import clock
from algotrade.repositories import codec, schema
from algotrade.repositories.base import AlreadyExists, Repository


class JobAlreadySucceeded(RuntimeError):
    """This job has already completed for this business date.

    Not an error condition -- it is the exactly-once guarantee firing, and the
    correct response is almost always to exit cleanly.
    """


class JobRunRepository(Repository):
    collection_name = "job_runs"

    @staticmethod
    def key(job_name: str, business_date: date, attempt: int) -> str:
        return f"{job_name}:{business_date.isoformat()}:{attempt}"

    def already_succeeded(self, job_name: str, business_date: date) -> bool:
        return self.collection.count_documents(
            {
                "job_name": job_name,
                "business_date": codec.to_mongo_date(business_date),
                "status": "success",
            },
            limit=1,
        ) > 0

    def next_attempt(self, job_name: str, business_date: date) -> int:
        latest = self.collection.find_one(
            {"job_name": job_name,
             "business_date": codec.to_mongo_date(business_date)},
            sort=[("attempt", -1)],
            projection={"attempt": 1},
        )
        return (latest["attempt"] + 1) if latest else 1

    def claim(
        self,
        job_name: str,
        business_date: date,
        *,
        now: datetime,
        github_run_id: str | None = None,
        code_commit: str = "",
    ) -> str:
        """Start a run, refusing if this date already succeeded.

        Raises:
            JobAlreadySucceeded: a successful run exists for this business date.
        """
        if self.already_succeeded(job_name, business_date):
            raise JobAlreadySucceeded(
                f"{job_name} already succeeded for {business_date}"
            )

        attempt = self.next_attempt(job_name, business_date)
        _id = self.key(job_name, business_date, attempt)
        self.insert({
            "_id": _id,
            "job_name": job_name,
            "business_date": codec.to_mongo_date(business_date),
            "attempt": attempt,
            "status": "running",
            "started_at": codec.to_mongo_instant(now),
            "github_run_id": github_run_id,
            "code_commit": code_commit,
        })
        return _id

    def complete(
        self, run_id: str, *, now: datetime, result: dict[str, Any] | None = None
    ) -> None:
        """Mark success and stamp the TTL.

        `expires_at` is set here rather than by a TTL on `started_at`, so the
        retention window is explicit and a financial collection could never
        acquire one by copying this pattern.
        """
        self._finish(run_id, "success", now=now, result=result)

    def fail(
        self, run_id: str, *, now: datetime, error: dict[str, Any] | None = None
    ) -> None:
        self._finish(run_id, "failed", now=now, error=error)

    def skip(self, run_id: str, *, now: datetime, reason: str) -> None:
        self._finish(run_id, "skipped", now=now, skip_reason=reason)

    def _finish(
        self,
        run_id: str,
        status: str,
        *,
        now: datetime,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        skip_reason: str | None = None,
    ) -> None:
        started = self.get(run_id)
        duration = None
        if started and started.get("started_at"):
            began = codec.from_mongo_instant(started["started_at"])
            duration = int((now - began).total_seconds() * 1000)

        changes: dict[str, Any] = {
            "status": status,
            "finished_at": codec.to_mongo_instant(now),
            "duration_ms": duration,
            "expires_at": codec.to_mongo_instant(
                now + timedelta(days=schema.TTL_DAYS["job_runs"])
            ),
        }
        if result is not None:
            changes["result"] = result
        if error is not None:
            changes["error"] = error
        if skip_reason is not None:
            changes["skip_reason"] = skip_reason
        self.update(run_id, changes)

    def succeeded_dates(self, job_name: str, *, since: date) -> set[date]:
        docs = self.collection.find(
            {
                "job_name": job_name,
                "status": "success",
                "business_date": {"$gte": codec.to_mongo_date(since)},
            },
            {"business_date": 1},
        )
        return {codec.from_mongo_date(d["business_date"]) for d in docs}

    def missing_dates(self, job_name: str, *, start: date, end: date) -> list[date]:
        """Weekdays in [start, end] with no successful run.

        The unit of work for every reconciliation-driven job. Weekends are
        excluded; holidays are not, because they are indistinguishable from a
        gap until NSE is asked (§10.9) -- the job discovers that and records a
        `skipped` run.
        """
        done = self.succeeded_dates(job_name, since=start)
        return [d for d in clock.weekdays_between(start, end) if d not in done]

    def last_success(self, job_name: str) -> dict[str, Any] | None:
        return self._decode(
            self.collection.find_one(
                {"job_name": job_name, "status": "success"},
                sort=[("business_date", -1)],
            )
        )

    def recent_failures(self, *, limit: int = 20) -> list[dict[str, Any]]:
        return self.find({"status": "failed"},
                         sort=[("started_at", -1)], limit=limit)

    def stuck(self, *, now: datetime, older_than_hours: int = 6) -> list[dict[str, Any]]:
        """Runs still 'running' well past any plausible duration -- a crashed
        worker leaves these behind and nothing else would notice."""
        cutoff = now - timedelta(hours=older_than_hours)
        return self.find({
            "status": "running",
            "started_at": {"$lt": codec.to_mongo_instant(cutoff)},
        })
