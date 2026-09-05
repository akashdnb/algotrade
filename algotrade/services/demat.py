"""Does the book agree with the demat?

Three checks now exist and they ask different questions. Confusing them is how
a wrong number survives:

* **Reconciliation** -- does the book agree with its own trade record?
* **Invariants** -- do the stored figures agree with each other?
* **This** -- does the book agree with *reality*?

The first two are arithmetic over our own data, so a fill that was never
recorded is invisible to both: the book is consistent with the executions it
knows about, and its figures agree with each other. Only the broker can say
that thirty shares left the account.

**Policy: the broker is the observation, the book is the record.** Every
disagreement is a `Finding` for a human to resolve through the normal recording
path. Nothing here writes, and nothing auto-corrects -- a broker glitch, a
symbol-mapping error or a series change must never silently rewrite an
execution.

Matching is on **ISIN**, not symbol. A symbol changes on a rename and on an
EQ->BE series move; the ISIN does not. Matching on symbol would report a
holding as both missing and unexpected the day HFCL moved series, which is
noise that trains you to ignore the check.

Pure functions over decoded rows: no repository, no clock, no I/O.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from algotrade.brokers.base import BrokerHolding
from algotrade.services.invariants import Report


def _by_isin(holdings: Iterable[BrokerHolding]) -> dict[str, BrokerHolding]:
    return {h.isin: h for h in holdings if h.isin}


def check_holdings(
    portfolio_id: str,
    *,
    book: Sequence[dict],
    demat: Sequence[BrokerHolding],
) -> Report:
    """Compare book holdings against the broker's.

    `book` rows are decoded `holdings` documents, each needing `symbol`,
    `isin` and `quantity`. A book row without an ISIN cannot be matched and is
    reported rather than skipped -- silently ignoring it would let an unmatched
    position look like agreement.
    """
    report = Report(portfolio_id=portfolio_id)
    theirs = _by_isin(demat)
    seen: set[str] = set()

    for row in book:
        symbol = row.get("symbol", "?")
        quantity = int(row.get("quantity") or 0)
        if quantity == 0:
            continue

        isin = row.get("isin") or ""
        report.checked += 1
        if not isin:
            report.add("unmatchable",
                       f"{symbol}: book holding has no ISIN, so it cannot be "
                       f"checked against the demat")
            continue

        seen.add(isin)
        held = theirs.get(isin)
        if held is None:
            report.add("missing_at_broker",
                       f"{symbol} ({isin}): book holds {quantity}, the demat "
                       f"has no such position")
        elif held.quantity != quantity:
            report.add("quantity_mismatch",
                       f"{symbol} ({isin}): book holds {quantity}, demat "
                       f"holds {held.quantity}")

    for isin, held in theirs.items():
        if isin in seen or held.quantity == 0:
            continue
        report.checked += 1
        report.add("untracked_at_broker",
                   f"{held.symbol} ({isin}): demat holds {held.quantity}, the "
                   f"book has no such position")

    return report
