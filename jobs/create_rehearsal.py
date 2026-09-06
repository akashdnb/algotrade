#!/usr/bin/env python
"""Create the rehearsal portfolio: a book to practise executing on.

    python -m jobs.create_rehearsal --database algotrade_rehearsal

**Why a portfolio and not the shadow.** The shadow's only job is being the
zero-discretion, zero-slippage control that makes `paper - shadow` readable as
the cost of discretion. Give it broker plumbing and it acquires its own
execution noise, and the baseline stops meaning anything (design §12).

**Why `paper_momentum_v1_002`.** `schema.py` constrains portfolio ids to
`^(paper|live|shadow)_...`, so there is no `rehearsal_` prefix to be had.
Inside the scratch database it cannot collide with production, and
`brokers/wiring` routes anything that is not `live_` to the stub -- so this
book cannot reach an exchange even if pointed at one.

Refuses to run against a real database. The point of a rehearsal is that
being wrong is free, which stops being true the moment it writes somewhere
that matters.

Exit codes: 0 created or already present, 1 refused, 2 configuration.
"""

from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.domain import money  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client, schema  # noqa: E402
from algotrade.repositories.base import AlreadyExists  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402

log = logging.getLogger("jobs.create_rehearsal")

PORTFOLIO_ID = "paper_momentum_v1_002"
LABEL = "Rehearsal (stub broker)"

#: Databases this may write to. Not a warning -- a refusal. A rehearsal that
#: can touch the paper or production book is not a rehearsal.
ALLOWED_DATABASES = frozenset({"algotrade_rehearsal", "algotrade_scratch"})

INITIAL_CAPITAL = Decimal("200000.00")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="algotrade_rehearsal")
    parser.add_argument("--capital", default=str(INITIAL_CAPITAL))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.database not in ALLOWED_DATABASES:
        log.error("refusing to create a rehearsal book in %r; allowed: %s",
                  args.database, ", ".join(sorted(ALLOWED_DATABASES)))
        return 1

    s = settings_mod.load()
    db = client.connect(s.mongodb_uri, database=args.database)

    # The scratch database starts empty, so the collections and their
    # validators have to exist before anything is written -- otherwise Mongo
    # creates them unvalidated and the rehearsal is not rehearsing the same
    # constraints production has.
    for collection in schema.ALL:
        db.create_collection(collection.name, validator=collection.validator) \
            if collection.name not in db.list_collection_names() else None
        for index in collection.indexes:
            db[collection.name].create_index(index.keys, **index.kwargs())

    repo = PortfolioRepository(db)
    now = clock.SystemClock().now()
    capital = money.rupees(args.capital)

    try:
        repo.insert({
            "_id": PORTFOLIO_ID,
            "mode": "paper",
            "strategy": "momentum",
            "strategy_version": "momentum_v1",
            "strategy_params_hash": "",
            "status": "active",
            "execution_mode": "manual",
            "currency": "INR",
            "initial_capital": capital,
            "monthly_contribution": money.ZERO,
            "cash": capital,
            "total_contributed": capital,
            "units": Decimal("2000.000"),
            "label": LABEL,
            "created_at": now,
        })
    except AlreadyExists:
        log.info("%s already exists in %s", PORTFOLIO_ID, args.database)
        return 0

    log.info("created %s in %s with %s", PORTFOLIO_ID, args.database, capital)
    log.info("point the dashboard at it with "
             "MONGODB_DATABASE=%s PORTFOLIO_ID=%s", args.database, PORTFOLIO_ID)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
