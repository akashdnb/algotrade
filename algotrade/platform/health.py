"""The dead-man switch (design §10.6).

A system cannot alert on its own total failure. If GitHub Actions is down, the
repository is disabled, the workflow is deleted, or the schedule is silently
dropped, nothing in-system runs -- so nothing in-system can tell you. The only
remedy is an external observer that alerts on the ABSENCE of a signal.

healthchecks.io, free tier. Configure the check in **cron mode** with the same
expression the daily workflow uses (`30 14 * * 1-5`, UTC) and a **12 hour
grace**.

**Not "period 1 day, grace 6 hours".** That was the original setting, and it
was wrong for two independent reasons, both of which fired in the first week
of live running:

* *Weekends.* A weekday-only schedule leaves ~72 hours between Friday's
  success and Monday's. A 26-hour threshold cannot survive that, so the check
  went down every Saturday for a system that was behaving perfectly. Confirmed
  2026-08-30: last success Fri 23:37 UTC, marked down Sun 01:37 UTC -- exactly
  26 hours, with the next run not due until Monday. Cron mode fixes this
  properly, because it knows the next firing is Monday and does not start
  counting until then.
* *Delay.* GitHub has delayed these scheduled runs by 9-12 hours (observed
  2026-08-27 onward, up from ~35 minutes the week before). A 6-hour grace
  alerts on a run that is merely late, which is the fastest way to teach
  yourself to ignore the one monitor that is not hosted on the infrastructure
  it watches.

The cost of the 12-hour grace is that a genuinely missed run is detected ~12
hours late rather than ~6. That is the right trade: a dead-man switch that
cries wolf weekly is worth less than one that is believed.

Every call is best-effort and never raises: a monitoring failure must not fail
a run that otherwise succeeded. A silent monitor is bad; a monitor that breaks
production is worse.
"""

from __future__ import annotations

import logging
from typing import Final

import requests

log = logging.getLogger(__name__)

TIMEOUT: Final[int] = 10
RETRIES: Final[int] = 3


class Heartbeat:
    """Signals start, success, and failure to an external watcher.

    healthchecks.io models one check as ONE base URL, with `/start` and `/fail`
    appended -- three separate URLs would be three separate checks, each with
    its own independent timeout, which is almost never what you want. So the
    endpoints are derived from the base by default. Explicit overrides are
    accepted for setups that genuinely do use distinct checks, or a provider
    with a different URL shape.
    """

    def __init__(
        self,
        ping_url: str,
        *,
        start_url: str = "",
        success_url: str = "",
        fail_url: str = "",
        timeout: int = TIMEOUT,
    ) -> None:
        self._url = ping_url.rstrip("/")
        self._start = start_url.rstrip("/")
        self._success = success_url.rstrip("/")
        self._fail = fail_url.rstrip("/")
        self._timeout = timeout

    @property
    def enabled(self) -> bool:
        return bool(self._url or self._start or self._success or self._fail)

    def _endpoint(self, suffix: str, override: str) -> str:
        if override:
            return override
        return f"{self._url}{suffix}" if self._url else ""

    def _ping(self, suffix: str = "", body: str = "", override: str = "") -> bool:
        url = self._endpoint(suffix, override)
        if not url:
            return False
        for attempt in range(1, RETRIES + 1):
            try:
                response = requests.post(
                    url, data=body[:10_000].encode(), timeout=self._timeout
                )
                if response.ok:
                    return True
                log.warning("heartbeat %s -> HTTP %s", suffix or "/", response.status_code)
            except requests.RequestException as exc:
                log.warning("heartbeat %s failed (%s/%s): %s",
                            suffix or "/", attempt, RETRIES, exc)
        return False

    def start(self) -> bool:
        """Signal that a run has begun, so run duration is measured."""
        return self._ping("/start", override=self._start)

    def success(self, summary: str = "") -> bool:
        """Signal success. This is what resets the timer."""
        return self._ping("", summary, override=self._success)

    def fail(self, reason: str = "") -> bool:
        """Signal failure explicitly, rather than waiting for the timeout.

        Turns "something is wrong" into "the daily job failed with X" and turns
        a 26-hour detection delay into an immediate one.
        """
        return self._ping("/fail", reason, override=self._fail)


class NullHeartbeat:
    """Used when no ping URL is configured, so callers need no branching."""

    enabled = False

    def start(self) -> bool:
        return False

    def success(self, summary: str = "") -> bool:
        return False

    def fail(self, reason: str = "") -> bool:
        return False


def from_settings(settings) -> Heartbeat | NullHeartbeat:  # noqa: ANN001
    if not settings.healthchecks_configured:
        return NullHeartbeat()
    return Heartbeat(
        settings.healthchecks_ping_url,
        start_url=settings.healthchecks_start_url,
        success_url=settings.healthchecks_success_url,
        fail_url=settings.healthchecks_fail_url,
    )
