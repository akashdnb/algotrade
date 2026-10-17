"""Load the adjusted price panel for production use.

The research path (`scripts/run_hypothesis.load_adjusted_panel`) rebuilds this
from the whole 16-year archive every time. Production needs the same
*computation* over a shorter window for momentum signals, but some consumers
such as historical zone detection need the complete adjusted archive.

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

#: How much history the production path loads, and refuses to run below.
MIN_SESSIONS: int = 400

#: Calendar days read per requested session. Deliberately generous: NSE trades
#: ~250 days a year, but holidays cluster and the integrity check rejects stale
#: duplicates, so realised density runs nearer 0.6. This is only a READ bound.
_CALENDAR_MULTIPLIER: float = 2.2


class InsufficientHistory(RuntimeError):
    """Fewer sessions available than the strategy's lookback requires."""


@dataclass(frozen=True, slots=True)
class PanelResult:
    panel: pl.DataFrame
    sessions: int
    first_session: date
    last_session: date
    #: Identifies exactly which data produced a signal, for reproducibility.
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
    sessions: int | None = MIN_SESSIONS,
    min_sessions: int = MIN_SESSIONS,
) -> PanelResult:
    """Load an adjusted panel.

    ``sessions`` defaults to the bounded production window used by momentum
    signals. Pass ``sessions=None`` to load the complete curated history. The
    latter is required by historical detectors whose lookback extends beyond
    the latest 400 sessions (for example monthly zone formation).

    Args:
        curated_dir: Parquet store written by `build_panel`.
        actions_dir: Corporate-action feed.
        through: Last session to include. None = everything available.
        sessions: Number of trailing trading sessions, or None for all history.
        min_sessions: Refuse to return fewer than this.
    """
    start = None
    if through is not None and sessions is not None:
        start = through - timedelta(days=int(sessions * _CALENDAR_MULTIPLIER))

    raw = store.read(curated_dir, start=start, end=through)
    if raw.is_empty():
        raise InsufficientHistory(
            f"no sessions in {curated_dir} for {start}..{through}"
        )

    # For bounded consumers, trim to exactly the requested number of sessions.
    # For historical consumers, sessions=None intentionally preserves the full
    # archive so long-term zone candidates cannot disappear at the window edge.
    all_sessions = raw["date"].unique().sort().to_list()
    if sessions is not None and len(all_sessions) > sessions:
        raw = raw.filter(pl.col("date") >= all_sessions[-sessions])

    available = raw["date"].n_unique()
    if available < min_sessions:
        raise InsufficientHistory(
            f"{available} sessions available, need {min_sessions}"
        )

    feed_raw = astore.load(actions_dir)
    feed = ca.classify(feed_raw).filter(pl.col("price_factor").is_not_null())
    chains = rc.merge_chains(
        rc.resolve_chains(ig.find_renames(raw)),
        rc.chains_from_isin(astore.isin_map(feed_raw)),
    )
    result = rc.reconcile(ig.find_actions(raw), feed, chains=chains)
    adjusted = adj.adjust(raw, adj.factors_from_reconciliation(result))
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
