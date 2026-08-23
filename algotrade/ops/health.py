"""System health as a single score, and the evidence behind it.

100 is healthy. Each problem deducts, and every deduction names what it is for
-- a score with no explanation is a number you learn to ignore, which is the
failure mode this whole system is built against.

The weights are not arbitrary. They rank by **how wrong a decision made on
this data would be**:

* A reconciliation failure is worth −40 because the book disagrees with its
  trade record: every NAV, weight and return derived from it is wrong, and
  acting on them is worse than acting on nothing.
* Stale market data is worth −25 because valuations silently describe the
  past while looking current.
* A failed job is worth −15 because the system self-heals, so one failure is
  usually a delay rather than damage -- but two in a row is not.
* A notification failure is worth −10: nothing is wrong with the *money*, but
  you have lost the channel that would tell you when something is.

GREEN means act normally. YELLOW means look before you trade. RED means the
numbers cannot be trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Final

GREEN: Final[str] = "GREEN"
YELLOW: Final[str] = "YELLOW"
RED: Final[str] = "RED"

#: Below this the numbers should not be acted on.
RED_BELOW: Final[int] = 60
YELLOW_BELOW: Final[int] = 90


@dataclass(frozen=True, slots=True)
class Deduction:
    points: int
    reason: str
    detail: str = ""

    def __str__(self) -> str:
        base = f"-{self.points:<3} {self.reason}"
        return f"{base}  ({self.detail})" if self.detail else base


@dataclass
class Health:
    score: int
    status: str
    deductions: list[Deduction] = field(default_factory=list)
    facts: dict = field(default_factory=dict)

    @property
    def explanation(self) -> str:
        if not self.deductions:
            return "all checks pass"
        return "; ".join(d.reason for d in self.deductions)

    @property
    def actionable(self) -> bool:
        """Whether the reported numbers can be acted on."""
        return self.status != RED

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "status": self.status,
            "explanation": self.explanation,
            "actionable": self.actionable,
            "deductions": [
                {"points": d.points, "reason": d.reason, "detail": d.detail}
                for d in self.deductions
            ],
            "facts": self.facts,
        }


def score(
    *,
    sessions_behind: int = 0,
    consecutive_job_failures: int = 0,
    missing_snapshots: int = 0,
    reconciliation_failures: int = 0,
    notification_failures: int = 0,
    stale_recommendation_days: int = 0,
    heartbeat_configured: bool = True,
    backup_age_days: int | None = None,
    backup_checked: bool = False,
) -> Health:
    """Compute the score from observed facts. Pure -- performs no I/O.

    Args:
        backup_age_days: Age of the newest backup, or None if there is none.
        backup_checked: Whether backups were looked for at all. "Not checked"
            and "checked and absent" are different facts, and scoring an
            unchecked system as if it had no backup would make every caller
            that does not care about backups look unhealthy.
    """
    deductions: list[Deduction] = []

    if reconciliation_failures:
        deductions.append(Deduction(
            40, "reconciliation failing",
            f"{reconciliation_failures} check(s); the book disagrees with the "
            f"trade record, so every derived number is wrong",
        ))

    if sessions_behind >= 2:
        deductions.append(Deduction(
            25, "market data stale",
            f"{sessions_behind} session(s) behind; valuations describe the past",
        ))
    elif sessions_behind == 1:
        deductions.append(Deduction(5, "market data one session behind"))

    if consecutive_job_failures >= 2:
        deductions.append(Deduction(
            15, "jobs failing repeatedly",
            f"{consecutive_job_failures} consecutive; self-healing is not working",
        ))
    elif consecutive_job_failures == 1:
        # One failure is the normal case the design absorbs.
        deductions.append(Deduction(5, "one job failure", "expected to self-heal"))

    if missing_snapshots:
        deductions.append(Deduction(
            min(15, 3 * missing_snapshots), "snapshots missing",
            f"{missing_snapshots} session(s) unvalued",
        ))

    if notification_failures:
        deductions.append(Deduction(
            10, "notifications failing",
            f"{notification_failures} in 7 days; you would not be told about "
            f"the next problem",
        ))

    if stale_recommendation_days >= 3:
        deductions.append(Deduction(
            10, "recommendation past expiry",
            f"{stale_recommendation_days} days old; the signal is no longer "
            f"a decision the strategy made",
        ))

    if not heartbeat_configured:
        deductions.append(Deduction(
            20, "no dead-man switch",
            "total failure would be silent -- nothing outside the system watches it",
        ))

    if backup_checked and backup_age_days is None:
        deductions.append(Deduction(
            15, "no backup", "Atlas M0 has none of its own"))
    elif backup_age_days is not None and backup_age_days > 14:
        deductions.append(Deduction(
            10, "backup stale", f"{backup_age_days} days old"))

    total = max(0, 100 - sum(d.points for d in deductions))
    status = (
        RED if total < RED_BELOW
        else YELLOW if total < YELLOW_BELOW
        else GREEN
    )
    return Health(
        score=total, status=status, deductions=deductions,
        facts={
            "sessions_behind": sessions_behind,
            "consecutive_job_failures": consecutive_job_failures,
            "missing_snapshots": missing_snapshots,
            "reconciliation_failures": reconciliation_failures,
            "notification_failures": notification_failures,
            "stale_recommendation_days": stale_recommendation_days,
            "heartbeat_configured": heartbeat_configured,
            "backup_age_days": backup_age_days,
            "backup_checked": backup_checked,
        },
    )
