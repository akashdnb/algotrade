#!/usr/bin/env python
"""Check the public price feed against NSE's official opens.

    python -m jobs.verify_feed                  # the latest archived session
    python -m jobs.verify_feed --on 2026-09-04
    python -m jobs.verify_feed --tolerance-bps 25

Sizing happens on a free, unlicensed feed (design §6). That is defensible only
while the feed demonstrably agrees with the exchange, and "demonstrably" means
a number that is re-measured, not an assurance written down once.

**This runs retrospectively, and deliberately so.** The bhavcopy for a session
lands hours after it. Comparing the feed's open for that same session against
the official file turns "the feed looked right in September" into a claim that
keeps being tested.

It checks the names that would actually be traded -- the current book plus any
open recommendation's targets -- rather than the whole universe. Three hundred
requests to a free endpoint every day is how access gets withdrawn, and a
symbol this portfolio will never hold tells you nothing about a fill you will
never make.

**A wrong symbol mapping surfaces here as a different company's price**, which
is an enormous gap rather than a plausible one. That is the second reason to
run it.

Exit codes: 0 agreement, 1 disagreements found, 2 configuration or no data.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.io import bhavcopy, feed  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.recommendations import (  # noqa: E402
    RecommendationRepository,
)
from algotrade.repositories.symbol_map import SymbolMapRepository  # noqa: E402
from algotrade.services import pricing, review  # noqa: E402

log = logging.getLogger("jobs.verify_feed")

#: Both series, because a scrip under surveillance is still one we hold --
#: HFCL sat in BE while this was written.
SERIES = ("EQ", "BE")


def latest_session(sessions_dir: Path) -> tuple[date, Path] | None:
    """The newest archived bhavcopy. None if the archive is empty."""
    best: tuple[date, Path] | None = None
    for path in sessions_dir.rglob("sec_bhavdata_full_*.csv"):
        try:
            day = bhavcopy.date_from_filename(path)
        except ValueError:
            continue
        if best is None or day > best[0]:
            best = (day, path)
    return best


def session_path(sessions_dir: Path, on: date) -> Path | None:
    name = f"sec_bhavdata_full_{on.strftime('%d%m%Y')}.csv"
    for path in sessions_dir.rglob(name):
        return path
    return None


def official_opens(path: Path) -> dict[str, "money.Decimal"]:
    frame = bhavcopy.read_file(path, series=SERIES)
    return {
        row["symbol"]: money.rupees(str(row["open"]))
        for row in frame.iter_rows(named=True)
        if row.get("open") is not None
    }


def symbols_of_interest(db, portfolio_id: str) -> list[str]:
    """What this portfolio would actually trade: the book plus open targets."""
    held = {h.symbol for h in HoldingRepository(db).book(portfolio_id)
            if h.quantity > 0}

    repo = RecommendationRepository(db)
    for doc in repo.history(portfolio_id, limit=6):
        rec = repo.get_set(doc["_id"])
        if rec is not None and rec.open_items:
            held |= set(review.target_membership(rec))
            break
    return sorted(held)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--on", default="", help="session date, YYYY-MM-DD")
    parser.add_argument("--tolerance-bps", type=int, default=50,
                        help="how far the feed may differ before it is a finding")
    parser.add_argument("--portfolio", default="", help="defaults to the configured one")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    s = settings_mod.load()
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolio_id = args.portfolio or s.default_portfolio_id
    if not portfolio_id:
        log.error("no portfolio: set DEFAULT_PORTFOLIO_ID or pass --portfolio")
        return 2

    if args.on:
        try:
            on = date.fromisoformat(args.on)
        except ValueError:
            log.error("--on must be YYYY-MM-DD, got %r", args.on)
            return 2
        path = session_path(s.sessions_dir, on)
    else:
        found = latest_session(s.sessions_dir)
        if found is None:
            log.error("no archived sessions under %s", s.sessions_dir)
            return 2
        on, path = found

    if path is None:
        log.error("no archived bhavcopy for %s -- nothing to check against", on)
        return 2

    symbols = symbols_of_interest(db, portfolio_id)
    if not symbols:
        log.info("%s holds nothing and has no open targets; nothing to check",
                 portfolio_id)
        return 0

    log.info("checking %d symbols against %s", len(symbols), path.name)
    quotes = pricing.resolve_opens(
        symbols, on=on,
        resolve=SymbolMapRepository(db).resolve,
        fetch=lambda tickers, day: feed.opens(tickers, on=day),
    )
    log.info(quotes.summary())

    official = official_opens(path)
    bad, unchecked = feed.verify(quotes.prices, official,
                                 tolerance_bps=args.tolerance_bps)

    # Reported, never merged into the pass. A symbol bhavcopy did not carry is
    # a price nobody verified, and counting it as agreement would let the
    # check report success for exactly the names it could not examine.
    if quotes.failures:
        log.info("%d not priced by the feed: %s",
                 len(quotes.failures), ", ".join(quotes.failures))
    if unchecked:
        log.info("%d absent from bhavcopy: %s",
                 len(unchecked), ", ".join(unchecked))

    if not bad:
        log.info("OK -- %d of %d agree within %d bps",
                 len(quotes.prices) - len(unchecked), len(symbols),
                 args.tolerance_bps)
        return 0

    log.error("%d disagreement(s) beyond %d bps:", len(bad), args.tolerance_bps)
    for finding in sorted(bad, key=lambda f: -f.bps):
        log.error("  %s", finding)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
