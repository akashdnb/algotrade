#!/usr/bin/env python
"""Fork a portfolio into a shadow -- an exact copy under a new id.

    python -m jobs.fork_portfolio --into shadow_momentum_v1_001 --dry-run
    python -m jobs.fork_portfolio --into shadow_momentum_v1_001

The shadow is the control group: from the fork onward it auto-fills every
instruction at the reference price, so `paper - shadow` is the cost of every
decision you made that the strategy did not ask for.

That subtraction is only meaningful if both books start identically, which is
why this copies rather than creates. It REFUSES if the source has already
diverged -- any fill with a broker order id, or at a price other than the
reference -- because copying a hand-entered fill would put the same human
decision on both sides of the comparison and quietly cancel it out.

Holdings are not copied. They are rebuilt from the copied executions and then
reconciled, so a fork that succeeded is a fork that provably reproduces the
source book rather than one that merely wrote documents.

Exit codes: 0 forked and consistent, 1 refused or inconsistent, 2 config.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from algotrade.config import settings as settings_mod  # noqa: E402
from algotrade.config import validation  # noqa: E402
from algotrade.domain import ids  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.repositories import client, codec  # noqa: E402
from algotrade.repositories.audit import AuditRepository  # noqa: E402
from algotrade.repositories.cash import CashRepository  # noqa: E402
from algotrade.repositories.executions import ExecutionRepository  # noqa: E402
from algotrade.repositories.holdings import HoldingRepository  # noqa: E402
from algotrade.repositories.portfolios import PortfolioRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.repositories.snapshots import SnapshotRepository  # noqa: E402
from algotrade.services import fork as fork_service  # noqa: E402
from algotrade.services.holdings import HoldingsService  # noqa: E402
from algotrade.services.reconciliation import ReconciliationService  # noqa: E402

log = logging.getLogger("jobs.fork_portfolio")

JOB_NAME = "fork_portfolio"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--from", dest="source", default=None,
                    help="default: DEFAULT_PORTFOLIO_ID")
    ap.add_argument("--into", required=True, help="the new portfolio id")
    ap.add_argument("--label", default=None,
                    help="default: the source label with a shadow suffix")
    ap.add_argument("--allow-divergent-source", action="store_true",
                    help="copy even though the source contains hand-entered "
                         "fills -- the comparison will understate discretion")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors
                if not f.key.startswith(("TELEGRAM", "HEALTHCHECKS"))]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    source_id = args.source or s.default_portfolio_id
    target_id = args.into
    if source_id == target_id:
        print("--into must differ from the source", file=sys.stderr)
        return 2

    # Both ends are guarded: a fork is a way to create a portfolio, and the
    # `live_` rule must not be reachable through a side door.
    try:
        validation.guard_portfolio(s, source_id)
        validation.guard_portfolio(s, target_id)
        target = ids.parse(target_id)
        source = ids.parse(source_id)
    except (settings_mod.ConfigError, ids.InvalidPortfolioId) as exc:
        print(exc, file=sys.stderr)
        return 2

    # Forking across strategies would compare two different things and call
    # the difference discretion.
    if target.strategy_key != source.strategy_key:
        print(f"strategy mismatch: {source_id} is {source.strategy_key}, "
              f"{target_id} is {target.strategy_key}", file=sys.stderr)
        return 2

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    portfolios = PortfolioRepository(db)

    source_doc = portfolios.get(source_id)
    if source_doc is None:
        print(f"no portfolio {source_id!r}", file=sys.stderr)
        return 1
    if portfolios.exists(target_id):
        print(f"{target_id} already exists -- a fork is not a merge. Drop it "
              f"first if you meant to start over.", file=sys.stderr)
        return 1

    # Documents can outlive a dropped portfolio doc; inserting into a
    # half-populated target would interleave two books.
    stale = {
        name: db[name].count_documents({"portfolio_id": target_id}, limit=1)
        for name in fork_service.FORKED_COLLECTIONS
    }
    if any(stale.values()):
        print(f"{target_id} has no portfolio document but does have data in: "
              f"{', '.join(n for n, c in stale.items() if c)}", file=sys.stderr)
        return 1

    # --- the honesty gate ---------------------------------------------------
    executions = ExecutionRepository(db)
    evidence = fork_service.discretion_evidence(
        executions.find({"portfolio_id": source_id}, sort=[("executed_on", 1)])
    )
    if evidence:
        print(f"\nREFUSED: {source_id} has already diverged from its "
              f"instructions ({len(evidence)} fill(s)):", file=sys.stderr)
        for line in evidence[:10]:
            print(f"  {line}", file=sys.stderr)
        if len(evidence) > 10:
            print(f"  ... and {len(evidence) - 10} more", file=sys.stderr)
        if not args.allow_divergent_source:
            print("\nCopying these would put the same decisions on both sides "
                  "of the comparison, so the shadow would understate what "
                  "your discretion cost. Pass --allow-divergent-source to "
                  "fork anyway and read the difference as post-fork only.",
                  file=sys.stderr)
            return 1
        print("\n--allow-divergent-source: copying anyway.", file=sys.stderr)

    counts = {
        name: db[name].count_documents({"portfolio_id": source_id})
        for name in fork_service.FORKED_COLLECTIONS
    }
    print(f"source     {source_id}  ({source_doc.get('label', '')})")
    print(f"target     {target_id}  mode={target.mode}")
    print(f"cash       Rs {source_doc.get('cash'):,}")
    print(f"units      {source_doc.get('units')}")
    for name, count in counts.items():
        print(f"  {name:<22} {count}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    now = clock.SystemClock().now()

    # Raw pymongo, not the repositories: this is a byte-for-byte copy with the
    # ids rewritten. Decoding to Decimal and re-encoding would be a lossless
    # round trip in theory and one more thing to be wrong in practice.
    written = {}
    for name in fork_service.FORKED_COLLECTIONS:
        docs = [
            fork_service.rewrite(d, source=source_id, target=target_id)
            for d in db[name].find({"portfolio_id": source_id})
        ]
        if docs:
            db[name].insert_many(docs, ordered=True)
        written[name] = len(docs)

    portfolios.insert({
        **source_doc,
        "_id": target_id,
        "mode": target.mode,
        "label": args.label or f"{source_doc.get('label', source_id)} (shadow)",
        "status": "active",
        # Provenance, so the comparison can never be read as two independent
        # track records that happen to look alike.
        "forked_from": source_id,
        "forked_at": codec.to_mongo_instant(now),
        "account_note": (
            f"Shadow of {source_id}. Every instruction is auto-filled at the "
            f"reference price; no human decides anything here. The difference "
            f"between the two books after {now.date()} is what discretion and "
            f"slippage cost."
        ),
    })

    for name, count in written.items():
        print(f"copied {count:>5}  {name}")

    # --- prove it -----------------------------------------------------------
    audit = AuditRepository(db)
    holdings_service = HoldingsService(
        executions=executions, holdings=HoldingRepository(db), audit=audit)
    rebuild = holdings_service.rebuild(target_id, now=now)
    print(f"\nrebuilt {len(rebuild.holdings)} position(s) from "
          f"{rebuild.executions_replayed} execution(s)")

    consistency = ReconciliationService(
        portfolios=portfolios, holdings_service=holdings_service,
        cash=CashRepository(db), executions=executions,
        recommendations=RecommendationRepository(db),
        snapshots=SnapshotRepository(db),
    )
    result = consistency.check(target_id)
    for finding in result.findings:
        print(f"  {finding}")

    audit.record(
        # `actor` is a closed enum in the collection validator -- system, user,
        # github-actions -- so the job name goes in the context, where it is
        # queryable without widening a schema that exists to keep the audit
        # trail's actor field meaningful.
        now=now, actor="system", action="portfolio.forked",
        entity_type="portfolio", entity_id=target_id, portfolio_id=target_id,
        after={"forked_from": source_id, "copied": written,
               "reconciled": result.ok},
        context={"job": JOB_NAME, "code_commit": s.code_commit,
                 "divergent_source": bool(evidence)},
        severity="info" if result.ok else "error",
    )

    if not result.ok:
        print("\nRECONCILIATION FAILED: the copy does not reproduce the "
              "source book. Do not trust this shadow.", file=sys.stderr)
        return 1

    print(f"\n{target_id} forked and reconciled.")
    print("Next: python -m jobs.value_portfolio --portfolio-id "
          f"{target_id} --rebuild-holdings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
