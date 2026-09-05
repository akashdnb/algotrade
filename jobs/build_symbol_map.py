#!/usr/bin/env python
"""Build or refresh `symbol_map` -- the identity table three namespaces share.

    python -m jobs.build_symbol_map --dry-run     # report, write nothing
    python -m jobs.build_symbol_map

Needs a live broker session, because Kite's instruments dump is the only
source of tradingsymbols and instrument tokens. Authorise on the dashboard
first; this reads the session it stored.

**Every identity change is reported, never applied silently.** A symbol whose
ISIN, tradingsymbol or token moved is either a corporate action worth knowing
about or a bug worth catching, and the two look identical until a human reads
them. `--dry-run` shows the changes without writing.

Exit codes: 0 written (or nothing to do), 1 refused, 2 configuration.
"""

from __future__ import annotations

import argparse
import base64
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.brokers import tokens  # noqa: E402
from algotrade.brokers.base import BrokerError  # noqa: E402
from algotrade.brokers.kite import KiteBroker  # noqa: E402
from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.io import actions as astore  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.broker_sessions import (  # noqa: E402
    BrokerSessionRepository,
)
from algotrade.repositories.symbol_map import SymbolMapRepository  # noqa: E402
from algotrade.services import symbols  # noqa: E402

log = logging.getLogger("jobs.build_symbol_map")


def _isins_from_actions(directory: Path) -> dict[str, str]:
    """symbol -> ISIN from the corporate-action feed.

    Best effort. The feed covers only names that ever had an action, and its
    absence must not stop the map being built -- a map without ISINs still
    carries the tradingsymbols and tokens, which is what addresses Kite.
    """
    if not directory.exists():
        log.info("no corporate-action cache at %s; skipping ISIN enrichment",
                 directory)
        return {}
    frame = astore.isin_map(astore.load(directory))
    if frame.is_empty():
        return {}
    # One ISIN per symbol: the most recent ex-date is the current pairing.
    latest = frame.sort("last_ex_date").group_by("symbol").last()
    return dict(zip(latest["symbol"].to_list(), latest["isin"].to_list()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report changes and coverage, write nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    s = settings_mod.load()
    if not (s.broker_api_key and s.broker_token_key):
        log.error("BROKER_API_KEY and BROKER_TOKEN_KEY must both be set")
        return 2

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    name = s.broker_name or "zerodha"
    now = clock.SystemClock().now()

    ciphertext = BrokerSessionRepository(db).live(name, now=now)
    if not ciphertext:
        log.error("no live broker session; authorise on the dashboard first")
        return 1

    try:
        key = base64.b64decode(s.broker_token_key, validate=True)
        broker = KiteBroker(api_key=s.broker_api_key,
                            access_token=tokens.decrypt(ciphertext, key=key))
        instruments = broker.instruments()
        demat = broker.holdings()
    except (BrokerError, ValueError, tokens.TokenError) as exc:
        log.error("could not read from the broker: %s", exc)
        return 1

    base = symbols.from_instruments(instruments)
    log.info("%d instrument rows -> %d NSE cash equities",
             len(instruments), len(base))

    mappings = symbols.enrich(
        base,
        isins=_isins_from_actions(s.data_dir / "raw" / "actions"),
        demat=demat,
    )
    identified, total = symbols.coverage(mappings)
    log.info("%d/%d carry an ISIN (%.1f%%)", identified, total,
             100.0 * identified / total if total else 0.0)

    repo = SymbolMapRepository(db)
    if args.dry_run:
        # Compare without writing, so the changes can be read before they
        # become the record.
        _report(repo.compare(mappings))
        log.info("dry run: nothing written")
        return 0

    changes = repo.refresh(mappings, on=now.date())
    _report(changes)
    log.info("wrote %d mappings", len(mappings))
    return 0


def _report(changes: list) -> None:
    if not changes:
        log.info("no identity changes")
        return
    log.info("%d identity change(s):", len(changes))
    for change in changes:
        log.info("  %s", change)


if __name__ == "__main__":
    raise SystemExit(main())
