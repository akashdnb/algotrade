#!/usr/bin/env python
"""Send the monthly rebalance alert.

The alert IS the deliverable: a recommendation nobody sees is a recommendation
nobody acts on. No orders are placed anywhere in this path.

    python -m jobs.rebalance_alert --set-id paper_momentum_v1_001:2026-09:r1
    python -m jobs.rebalance_alert --set-id ... --dry-run

Deduped by (kind, portfolio, date), so re-running the workflow cannot send a
second copy. Regenerating as a new revision DOES send again, which is correct:
r2 is a different instruction from r1.
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
from algotrade.domain import money  # noqa: E402
from algotrade.domain.models import Action, SetStatus  # noqa: E402
from algotrade.platform import clock  # noqa: E402
from algotrade.platform.telegram import TelegramClient  # noqa: E402
from algotrade.repositories import client  # noqa: E402
from algotrade.repositories.notifications import NotificationRepository  # noqa: E402
from algotrade.repositories.rebalance_runs import RebalanceRunRepository  # noqa: E402
from algotrade.repositories.recommendations import RecommendationRepository  # noqa: E402
from algotrade.services.notification import NotificationService, render_rebalance  # noqa: E402

log = logging.getLogger("jobs.rebalance_alert")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--set-id", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    s = settings_mod.load()
    logging.basicConfig(level=getattr(logging, s.log_level, logging.INFO),
                        format="%(levelname)-7s %(message)s")

    report = validation.validate(s)
    blocking = [f for f in report.errors if not f.key.startswith("HEALTHCHECKS")]
    if blocking:
        for finding in blocking:
            print(finding, file=sys.stderr)
        return 2

    db = client.connect(s.mongodb_uri, database=s.mongodb_database)
    recs = RecommendationRepository(db)
    rec = recs.get_set(args.set_id)
    if rec is None:
        print(f"no recommendation set {args.set_id!r}", file=sys.stderr)
        return 1

    run = RebalanceRunRepository(db).get(rec.rebalance_run_id)
    turnover = run.get("turnover_estimate") if run else None

    # `target` is what the book should become, `held` what it was. Both are
    # recorded on the rebalance run precisely so this does not have to be
    # inferred from the instructions.
    def _symbols(rows):
        return {r["symbol"] if isinstance(r, dict) else r for r in (rows or [])}
    target = _symbols((run or {}).get("target"))
    held = _symbols((run or {}).get("current"))

    buys = [i for i in rec.items if i.action == Action.BUY]
    sells = [i for i in rec.items if i.action == Action.SELL]
    deployed = money.sum_rupees(i.estimated_value for i in buys)

    text = render_rebalance(
        portfolio_id=rec.portfolio_id,
        period=rec.period,
        set_id=rec.set_id,
        buys=len(buys), sells=len(sells), holds=len(rec.holds),
        deployed=deployed,
        charges=rec.estimated_total_charges,
        turnover=turnover,
        # Classified against the TARGET, not by side. A sell of a name the
        # target keeps is a trim, and listing it under "Exiting" tells you to
        # close a position the strategy wants held.
        entering=[i.symbol for i in buys if i.symbol not in held],
        leaving=[i.symbol for i in sells if i.symbol not in target],
        adjusting=sorted({i.symbol for i in rec.items}
                         & target & held),
        expires_on=rec.expires_on,
    )

    if args.dry_run:
        print(text)
        return 0

    sent = NotificationService(
        repository=NotificationRepository(db),
        client=(TelegramClient(s.telegram_bot_token, s.telegram_chat_id)
                if s.telegram_configured else None),
        chat_id=s.telegram_chat_id,
    ).send(kind=f"rebalance_r{rec.revision}", text=text,
           now=clock.SystemClock().now(),
           portfolio_id=rec.portfolio_id, business_date=rec.as_of_session)

    if sent is None:
        print(f"already alerted for {rec.set_id}")
        return 0

    # NOTIFIED, not APPROVED: the human decision has not been made yet.
    if rec.status == SetStatus.PENDING:
        recs.update(rec.set_id, {"status": SetStatus.NOTIFIED.value})
    print(f"rebalance alert sent for {rec.set_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
