#!/usr/bin/env python
"""Nightly shadow-portfolio run: fetch, recompute, parity-check, ledger, alert.

Signal parity + paper P&L only (PROJECT.md §9, §12) -- no UI, no accept/decline,
no live portfolio, no broker orders. Those are deferred to Phase 0d / full §9.
See docs/superpowers/specs/2026-08-23-shadow-portfolio-design.md.

Idempotent: re-running an already-processed session is safe (the fetch step
is a cache hit, per `nse.download_to`). A missed firing (laptop asleep, no
network) self-heals on the next run -- `load_panel` reads everything present,
so a later run picks up whatever session was fetched in the meantime.

    python scripts/shadow_run.py
    python scripts/shadow_run.py --date 2026-08-24   # manual catch-up/testing
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import polars as pl

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.core import costs  # noqa: E402
from algotrade.io import indices, nse, store  # noqa: E402
from algotrade.io.archive import load_panel  # noqa: E402
from algotrade.shadow import ledger, parity, signal  # noqa: E402
from algotrade.shadow import store as shadow_store  # noqa: E402

MIRROR = REPO / "data" / "raw" / "nse-data-bank" / "data"
BACKFILL = REPO / "data" / "raw" / "backfill"
PANEL_DIRS = [MIRROR, BACKFILL]
CURATED = REPO / "data" / "curated"
SHADOW = REPO / "data" / "shadow"
INDEX_DIR = REPO / "data" / "raw" / "indices"
CONFIG = REPO / "config" / "costs.yaml"
CAPITAL = 200_000.0

# Reused, not reimplemented -- confirmed generic (webhook via env var, no
# company coupling) despite living under cap-repos/. See design spec.
SLACK_PUSH = Path.home() / "Developer" / "cap-repos" / "grafana_automation" / "slack_push.py"
# launchd's environment doesn't source ~/.zshrc, so SLACK_WEBHOOK_URL lives
# here instead -- gitignored by the existing `.env.*` pattern in .gitignore.
SHADOW_ENV = REPO / "config" / ".env.shadow"

RUN_LOG = "run_log.jsonl"
DISCREPANCIES_LOG = "discrepancies.jsonl"


@dataclass
class FetchOutcome:
    status: str  # "fetched" | "holiday" | "failed"
    detail: str = ""


def is_weekend(day: date) -> bool:
    return day.weekday() >= 5


def fetch_session(day: date, dest_dir: Path) -> FetchOutcome:
    """Pull one session's bhavcopy, distinguishing a holiday from a failure.

    A 404 from NSE's archive endpoint is a direct, trustworthy "no session
    that day" -- unlike `nsefin`'s holiday calendar, which fails silently
    (§10.9) -- so it is not something to alert on.
    """
    try:
        nse.download_to(day, dest_dir)
    except nse.NseFetchError as exc:
        if "404" in str(exc):
            return FetchOutcome("holiday", str(exc))
        return FetchOutcome("failed", str(exc))
    return FetchOutcome("fetched")


def sync_panel(
    day: date,
    *,
    panel_dirs: list[Path] = PANEL_DIRS,
    fetch_dir: Path = BACKFILL,
    curated_dir: Path = CURATED,
) -> tuple[str, pl.DataFrame | None, str]:
    """Fetch `day`, extend the curated panel, and return it through `day`.

    Returns (status, panel-through-day, detail). `status` is "ok", "holiday",
    or "failed"; `panel` is None unless "ok". A thin composition of
    `fetch_session`, `load_panel`, and `store.write` -- each already tested
    (test_archive_and_nse.py, test_bhavcopy.py, test_store.py) -- so this
    function is exercised at run time rather than re-tested here.
    """
    outcome = fetch_session(day, fetch_dir)
    if outcome.status != "fetched":
        return outcome.status, None, outcome.detail

    panel, report = load_panel(panel_dirs, end=day)
    if panel.is_empty():
        return "failed", None, "panel empty after a reported-successful fetch"

    store.write(
        panel,
        directory=curated_dir,
        rejected=report.rejected_dates,
        source_dirs=[str(p) for p in panel_dirs],
    )
    return "ok", panel, ""


def _load_env_file(path: Path) -> dict:
    """A minimal KEY=VALUE reader. Not a general .env parser -- one file, one
    purpose: getting SLACK_WEBHOOK_URL to a subprocess launchd won't hand an
    interactive shell's environment to."""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def alert(message: str) -> None:
    """Best-effort Slack notification. Never raises: a broken alert channel
    must not crash a run that otherwise completed, and must not be confused
    with a broken pipeline in the run log."""
    if not SLACK_PUSH.exists():
        print(f"[alert, not sent -- slack_push missing at {SLACK_PUSH}] {message}")
        return
    env = {**os.environ, **_load_env_file(SHADOW_ENV)}
    try:
        subprocess.run(
            [sys.executable, str(SLACK_PUSH), message],
            env=env, capture_output=True, timeout=30, check=False,
        )
    except Exception as exc:  # noqa: BLE001 -- alerting must never crash the run
        print(f"[alert failed: {exc}] {message}")


def git_commit(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def run_once(
    *,
    today: date,
    shadow_dir: Path = SHADOW,
    capital: float = CAPITAL,
    repo: Path = REPO,
    panel_dirs: list[Path] | None = None,
    fetch_dir: Path | None = None,
    curated_dir: Path | None = None,
    sync_fn: Callable[[date], tuple[str, pl.DataFrame | None, str]] | None = None,
    signal_fn: Callable[[pl.DataFrame], pl.DataFrame] = signal.deployed_weights,
    index_returns_fn: Callable[[date], pl.DataFrame] | None = None,
    alert_fn: Callable[[str], None] = alert,
) -> dict:
    """Run one nightly cycle. Returns the summary dict also written to the
    run log, so a caller (or a test) can assert on the outcome directly."""
    panel_dirs = panel_dirs or PANEL_DIRS
    fetch_dir = fetch_dir or BACKFILL
    curated_dir = curated_dir or CURATED
    sync_fn = sync_fn or (
        lambda day: sync_panel(
            day, panel_dirs=panel_dirs, fetch_dir=fetch_dir, curated_dir=curated_dir
        )
    )
    index_returns_fn = index_returns_fn or (
        lambda day: indices.benchmark_series(INDEX_DIR, end=day).select("date", "ret")
    )

    commit = git_commit(repo)
    record: dict = {"session": str(today), "commit": commit}

    if is_weekend(today):
        record["status"] = "skipped: weekend"
        shadow_store.append_jsonl(shadow_dir / RUN_LOG, record)
        return record

    status, panel, detail = sync_fn(today)
    if status == "holiday":
        record["status"] = "skipped: no data (holiday)"
        shadow_store.append_jsonl(shadow_dir / RUN_LOG, record)
        return record
    if status != "ok":
        record["status"] = "failed: fetch"
        record["detail"] = detail
        shadow_store.append_jsonl(shadow_dir / RUN_LOG, record)
        alert_fn(f"shadow_run {today}: fetch failed -- {detail}")
        return record

    weights = signal_fn(panel)
    old_weights = shadow_store.read_weights_log(directory=shadow_dir)
    new_session = weights["date"].max() if weights.height else today
    discrepancies = parity.diff_weights(old_weights, weights, new_session=new_session)

    if discrepancies.height:
        for row in discrepancies.iter_rows(named=True):
            shadow_store.append_jsonl(
                shadow_dir / DISCREPANCIES_LOG, {**row, "session": str(today), "commit": commit}
            )
        commit_short = commit[:8] if commit != "unknown" else commit
        alert_fn(
            f"shadow_run {today}: {discrepancies.height} parity discrepancy(ies) "
            f"found -- see data/shadow/{DISCREPANCIES_LOG} (commit {commit_short})"
        )

    # Advance the baseline regardless of the outcome above (design spec, step
    # 6): otherwise an explained fix re-triggers the same "discrepancy" every
    # night forever. The discrepancy record itself is permanent; only the
    # comparison point moves forward.
    shadow_store.write_weights_log(weights, directory=shadow_dir, commit=commit)

    card = costs.load_rate_cards(CONFIG)["delivery"]
    slippage = costs.load_slippage(CONFIG)
    shadow_curve = ledger.shadow_full_curve(
        weights, panel, capital=capital, card=card, slippage=slippage
    )
    benchmark = ledger.benchmark_curve(index_returns_fn(today), capital=capital)
    equity = ledger.build_ledger(shadow_curve, benchmark)
    shadow_store.write_equity(equity, directory=shadow_dir)

    record["status"] = "ok"
    record["discrepancies"] = discrepancies.height
    record["sessions"] = panel["date"].n_unique() if panel.height else 0
    shadow_store.append_jsonl(shadow_dir / RUN_LOG, record)
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=None,
        help="session to run for (default: today)",
    )
    args = ap.parse_args()
    today = args.date or date.today()

    print(f"shadow run for {today} ...", flush=True)
    record = run_once(today=today)
    print(record)
    return 0 if record["status"].startswith(("ok", "skipped")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
