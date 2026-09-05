#!/usr/bin/env python
"""Copy a live book into a scratch database, to rehearse against.

    python scripts/seed_scratch_db.py --into algotrade_rehearsal
    python scripts/seed_scratch_db.py --into algotrade_rehearsal --reset

**Why this exists.** As of 2026-09-05 the entire monthly cycle has never run in
the configuration it will actually run in. `notifications` contains only
`daily_summary` and `system_health` -- **no rebalance alert has ever been
sent** -- and `audit_logs` has no `recommendation.approved` and no skips. The
thirty executions on the books were auto-filled by hand, locally. The first
automatic rebalance is due on the first October session, and it will be the
first exercise of `rebalance_alert`, the shadow's `record_execution
--auto-fill`, the dashboard write path, and reconciliation after a real fill.

Finding out on the day is a worse plan than finding out now.

`validation` already anticipates this: a database that is not the environment's
default, and does not belong to another environment, is a **warning** hinted
"intentional for a scratch database". So every job runs against the copy with
`MONGODB_DATABASE` overridden and nothing else changed.

**What is NOT copied**, deliberately:

* `job_runs` -- claims are keyed `(job, business_date)`; copying them would
  make the rehearsal collide with real ledger history, and it also carries the
  acceptance gate's fault-injection evidence, which must not be duplicated.
* `audit_logs` and `notifications` -- a rehearsal must not be able to produce
  something that later reads as a record of the real book.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.repositories import client  # noqa: E402

#: Everything the monthly cycle reads. `signals` is essential -- a
#: recommendation cannot be generated without a stored ranking.
COPY: tuple[str, ...] = (
    "portfolios", "holdings", "executions", "cash_transactions",
    "portfolio_snapshots", "recommendations", "rebalance_runs", "signals",
)
SKIP: tuple[str, ...] = ("job_runs", "audit_logs", "notifications")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--into", required=True, help="scratch database name")
    ap.add_argument("--reset", action="store_true",
                    help="drop the copied collections first")
    args = ap.parse_args()

    s = settings_mod.load()
    if args.into == s.mongodb_database:
        print(f"--into is the LIVE database ({args.into}). Refusing.",
              file=sys.stderr)
        return 2
    # A scratch name that happens to be another environment's would be caught
    # by `validation` when the jobs run, but refusing here is cheaper than
    # discovering it after the copy.
    from algotrade.config.validation import DEFAULT_DATABASE
    if args.into in set(DEFAULT_DATABASE.values()):
        print(f"{args.into!r} is an environment's real database. Refusing.",
              file=sys.stderr)
        return 2

    source = client.connect(s.mongodb_uri, database=s.mongodb_database)
    target = client.connect(s.mongodb_uri, database=args.into)
    print(f"{s.mongodb_database}  ->  {args.into}")

    # Validators and indexes first, so the rehearsal hits the same constraints
    # production does. That is not decoration: the actor enum on `audit_logs`
    # is what caught a bad write during the shadow fork.
    for name, actions in client.apply_schema(target).items():
        if actions:
            print(f"  schema {name}: {', '.join(actions)}")

    for name in COPY:
        docs = list(source[name].find())
        if args.reset:
            target[name].delete_many({})
        existing = target[name].count_documents({})
        if existing and not args.reset:
            print(f"  {name:<22} SKIPPED -- {existing} docs present (--reset to replace)")
            continue
        if docs:
            target[name].insert_many(docs, ordered=False)
        print(f"  {name:<22} {len(docs):>5} copied")
    print(f"  {'(not copied)':<22} {', '.join(SKIP)}")

    print(f"""
Rehearse with MONGODB_DATABASE overridden -- nothing else changes:

  export MONGODB_DATABASE={args.into}

  # 1. is a rebalance due? (--force, since September has not closed yet)
  python -m jobs.generate_recommendations --check-only

  # 2. generate one, as the workflow would
  python -m jobs.generate_recommendations --portfolio-id paper_momentum_v1_001 \\
      --period 2026-09 --force

  # 3. the alert nobody has ever seen. --dry-run renders without sending.
  python -m jobs.rebalance_alert --set-id <id> --dry-run

  # 4. the shadow fills itself, exactly as rebalance.yml will
  python -m jobs.generate_recommendations --portfolio-id shadow_momentum_v1_001 \\
      --period 2026-09 --force
  python -m jobs.record_execution --set-id <shadow id> --auto-fill

  # 5. record ONE fill by hand at a price that is NOT the reference, so
  #    slippage is non-zero and the discretion split has something to divide
  python -m jobs.record_execution --set-id <paper id> --symbol <SYM> \\
      --side sell --quantity <n> --price <different> --charges <c>

  # 6. and check the books still agree
  python -m jobs.value_portfolio --all-active --reconcile-only

Drop it afterwards:  mongosh --eval 'db.getSiblingDB("{args.into}").dropDatabase()'
""")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
