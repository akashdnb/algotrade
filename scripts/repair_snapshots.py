#!/usr/bin/env python
"""Delete pre-inception snapshots and rebuild the peak/drawdown series.

    python scripts/repair_snapshots.py                    # dry run, all active
    python scripts/repair_snapshots.py --apply
    python scripts/repair_snapshots.py --portfolio-id shadow_momentum_v1_001

**Dry run by default.** This deletes stored financial records, and the reason
that is defensible here is narrow: the rows are demonstrably fabricated rather
than merely stale.

`value_portfolio --backfill N` marks the CURRENT holdings at each target date's
prices. For a genuinely missed recent session that is correct -- the book did
not change. For a date before the book existed it invents history, and the
first daily run did exactly that: snapshots for 2026-08-14..08-19 carrying all
thirty positions and zero cash, when the first execution was 2026-08-20 and the
portfolio held nothing but cash.

Two things follow from those rows, and deleting them is not enough to undo
either:

* **The drawdown peak is anchored to a high that never happened.** `new_peak`
  ratchets up and is never revised, so 100.5550 -- recorded on 2026-08-18, a
  date the portfolio did not exist -- propagated into every later snapshot as
  100.5600 and became the denominator of every drawdown since.
* **The History page measured return from that invented starting point**,
  which is why it disagreed with Summary about whether the book was up or down.

So the peak and drawdown are recomputed for the surviving rows, in date order,
seeded exactly as a fresh run would seed them -- `peak_unit_nav()` over an
empty collection is ZERO, so the first surviving snapshot's peak is its own
unit NAV. That reproduces what the system would have written had the invented
rows never existed, rather than substituting a nicer definition.

`jobs/value_portfolio.py` will not create these rows again: `valuable_dates`
refuses any target before the ledger's first entry.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.domain import money, valuation as val  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client, codec  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402

log = logging.getLogger("scripts.repair_snapshots")


def repair(db, portfolio_id: str, *, apply: bool) -> int:
    inception = CashRepository(db).inception(portfolio_id)
    if inception is None:
        print(f"{portfolio_id}: never funded, nothing to repair")
        return 0

    rows = list(db["portfolio_snapshots"]
                .find({"portfolio_id": portfolio_id}).sort("date", 1))
    bogus = [r for r in rows if codec.from_mongo_date(r["date"]) < inception]
    keep = [r for r in rows if codec.from_mongo_date(r["date"]) >= inception]

    print(f"\n{portfolio_id}")
    print(f"  inception          {inception}")
    print(f"  snapshots          {len(rows)}  ({len(bogus)} before inception)")
    for r in bogus:
        print(f"    DELETE {r['date']}  unit_nav={codec.from_mongo_money(r['unit_nav'])}"
              f"  positions={len(r.get('positions', []))}")
    if not keep:
        print("  nothing would survive -- refusing")
        return 1

    # Seeded at BASE_UNIT_NAV, not at zero.
    #
    # A fresh sequential run seeds from `peak_unit_nav()`, which over an empty
    # collection is ZERO, so the first snapshot's peak becomes its own unit NAV
    # and its drawdown is 0 by construction. That is wrong whenever the book is
    # already underwater on the first session it is valued: here 2026-08-20
    # closed at 98.37 and would have reported a drawdown of zero, hiding a real
    # 1.6% loss.
    #
    # Unit NAV is 100.00 at inception by construction -- `units` is
    # `initial_capital / BASE_UNIT_NAV` -- so the running peak can never
    # honestly start below 100. Reproducing the current behaviour here would
    # be reproducing a second bug while repairing the first.
    peak = val.BASE_UNIT_NAV
    changes = []
    for r in keep:
        unit_nav = codec.from_mongo_money(r["unit_nav"])
        peak = val.new_peak(unit_nav=unit_nav, peak=peak)
        dd = val.drawdown(unit_nav=unit_nav, peak=peak)
        was_peak = codec.from_mongo_money(r.get("peak_unit_nav"))
        was_dd = r.get("drawdown")
        if was_peak != peak or abs((was_dd or 0.0) - dd) > 1e-12:
            changes.append((r["_id"], r["date"], was_peak, peak, was_dd, dd))

    print(f"  peak/drawdown      {len(changes)} row(s) to correct")
    for _id, day, wp, np_, wd, nd in changes[:6]:
        print(f"    {day}  peak {wp} -> {np_}   dd {wd:+.4%} -> {nd:+.4%}")
    if len(changes) > 6:
        print(f"    ... and {len(changes) - 6} more")

    if not apply:
        return 0

    if bogus:
        db["portfolio_snapshots"].delete_many(
            {"_id": {"$in": [r["_id"] for r in bogus]}})
    for _id, _day, _wp, new_peak, _wd, new_dd in changes:
        db["portfolio_snapshots"].update_one(
            {"_id": _id},
            {"$set": {"peak_unit_nav": codec.to_mongo_money(new_peak),
                      "drawdown": new_dd}})

    AuditRepository(db).record(
        now=clock.SystemClock().now(), actor="system",
        action="snapshots.repaired", entity_type="portfolio",
        entity_id=portfolio_id, portfolio_id=portfolio_id,
        after={"deleted": [str(codec.from_mongo_date(r["date"])) for r in bogus],
               "recomputed": len(changes), "inception": str(inception)},
        context={"job": "repair_snapshots",
                 "why": "pre-inception snapshots marked current holdings at "
                        "past prices; peak was anchored to an invented high"},
        severity="warning",
    )
    print(f"  APPLIED: {len(bogus)} deleted, {len(changes)} recomputed")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--portfolio-id", default=None, help="default: all active")
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    s = settings_mod.load()
    db = client.connect(s.mongodb_uri, database=s.mongodb_database)

    ids = ([args.portfolio_id] if args.portfolio_id
           else PortfolioRepository(db).active_ids())
    rc = 0
    for portfolio_id in ids:
        rc |= repair(db, portfolio_id, apply=args.apply)
    if not args.apply:
        print("\n--dry-run: nothing written. Re-run with --apply.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
