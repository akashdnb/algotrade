"""Load the adjusted price panel for production use.

The research path (`scripts/run_hypothesis.load_adjusted_panel`) rebuilds this
from the whole 16-year archive every time. Production needs the same
*computation* over a much shorter window: 12-1 momentum reaches back 252 + 21
sessions, so ~320 sessions is sufficient and a 16-year rebuild is wasted work.

**The adjustment logic itself is not reimplemented.** The same
`core.adjust` / `core.reconcile` / `core.integrity` functions run here as in
the backtest, because a second implementation of corporate-action adjustment
is precisely how live and backtested numbers drift apart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from algotrade.core import actions as ca
from algotrade.core import adjust as adj
from algotrade.core import integrity as ig
from algotrade.core import reconcile as rc
from algotrade.io import archive, store
from algotrade.io import actions as astore

log = logging.getLogger(__name__)

#: 252 + 21 = 273 for momentum, plus 20 for the universe window, plus headroom
#: for holidays. Below this the signal cannot be computed at all.
MIN_SESSIONS: int = 320

#: Calendar days to read for a given session count. ~250 sessions/year.
_CALENDAR_MULTIPLIER: float = 1.55


class InsufficientHistory(RuntimeError):
    """Fewer sessions available than the strategy's lookback requires.

    Raised rather than returning a short panel: a momentum feature computed on
    too little history is null for every symbol, which would silently produce
    an empty book rather than an obvious failure.
    """


@dataclass(frozen=True, slots=True)
class PanelResult:
    panel: pl.DataFrame
    sessions: int
    first_session: date
    last_session: date
    #: Identifies exactly which data produced a signal, for reproducibility
    #: (§9.2). Stored on every signal and rebalance run.
    manifest_hash: str


def _fingerprint(panel: pl.DataFrame) -> str:
    """Stable hash of the sessions and symbols that went into a panel."""
    import hashlib

    days = panel["date"].unique().sort().to_list()
    payload = f"{len(days)}|{days[0]}|{days[-1]}|{panel['symbol'].n_unique()}|{panel.height}"
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()[:16]


def load(
    *,
    curated_dir: Path,
    actions_dir: Path,
    through: date | None = None,
    sessions: int = MIN_SESSIONS,
    min_sessions: int = MIN_SESSIONS,
) -> PanelResult:
    """Adjusted panel covering roughly the last `sessions` trading days.

    Args:
        curated_dir: Parquet store written by `build_panel`.
        actions_dir: Corporate-action feed.
        through: Last session to include. None = everything available.
        sessions: How many trading sessions of history to load.
        min_sessions: Refuse to return fewer than this.

    Raises:
        InsufficientHistory: not enough sessions to compute the signal.
    """
    start = None
    if through is not None:
        start = through - timedelta(days=int(sessions * _CALENDAR_MULTIPLIER))

    raw = store.read(curated_dir, start=start, end=through)
    if raw.is_empty():
        raise InsufficientHistory(
            f"no sessions in {curated_dir} for {start}..{through}"
        )

    available = raw["date"].n_unique()
    if available < min_sessions:
        raise InsufficientHistory(
            f"{available} sessions available, need {min_sessions} "
            f"(12-1 momentum reaches back 252 + 21 sessions)"
        )

    feed_raw = astore.load(actions_dir)
    feed = ca.classify(feed_raw).filter(pl.col("price_factor").is_not_null())
    chains = rc.merge_chains(
        rc.resolve_chains(ig.find_renames(raw)),
        rc.chains_from_isin(astore.isin_map(feed_raw)),
    )
    result = rc.reconcile(ig.find_actions(raw), feed, chains=chains)
    adjusted = adj.adjust(raw, adj.factors_from_reconciliation(result))
    # Session grid, so padding is in sessions rather than calendar days.
    panel = adj.drop_windows(
        adjusted,
        adj.unadjusted_windows(result, sessions=raw["date"].unique().sort()),
    )

    days = panel["date"].unique().sort().to_list()
    return PanelResult(
        panel=panel,
        sessions=len(days),
        first_session=days[0],
        last_session=days[-1],
        manifest_hash=_fingerprint(panel),
    )


def rebuild_curated(
    *, session_dirs: list[Path], curated_dir: Path, through: date | None = None
) -> dict:
    """Rebuild the curated Parquet store from raw session CSVs.

    The curated panel is a CACHE, never a source of truth (`io/store`). In CI
    it is restored from `actions/cache` and refreshed here after new sessions
    land; locally it is rebuilt on demand.
    """
    panel, report = archive.load_panel(session_dirs, end=through)
    if panel.is_empty():
        raise InsufficientHistory(f"no sessions found under {session_dirs}")

    manifest = store.write(
        panel,
        directory=curated_dir,
        rejected=report.rejected_dates,
        source_dirs=[str(p) for p in session_dirs],
    )
    log.info("curated panel rebuilt: %s sessions, %s rows",
             manifest.sessions, manifest.rows)
    return {
        "sessions": manifest.sessions,
        "rows": manifest.rows,
        "first_session": manifest.first_session,
        "last_session": manifest.last_session,
        "rejected": len(manifest.rejected_sessions),
    }
