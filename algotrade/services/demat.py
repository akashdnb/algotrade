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

**Matching prefers ISIN and falls back to symbol.** A symbol changes on a
rename and on an EQ->BE series move; the ISIN does not, so ISIN matching is the
one that will not report a holding as both missing and unexpected the day HFCL
changes series.

But ISIN is not always available. Kite reports it on every holding; our own
book usually does not, because the current session format carries no ISIN and
nothing on the recommendation path populates one (see
`repositories/symbol_map.py`). ISIN-only matching would therefore report every
position as unmatchable -- technically honest and completely useless.

So each row records the basis it was matched on, and a symbol match says so.
That keeps the weaker comparison visible instead of letting it pass for the
stronger one.

Pure functions over decoded rows: no repository, no clock, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from algotrade.brokers.base import BrokerHolding
from algotrade.services.invariants import Report


#: How a book row was paired with a demat row.
BY_ISIN = "isin"
BY_SYMBOL = "symbol"


def _match(
    row: dict,
    *,
    by_isin: dict[str, BrokerHolding],
    by_symbol: dict[str, BrokerHolding],
) -> tuple[BrokerHolding | None, str]:
    """The demat row for this book row, and the basis it was found on.

    ISIN first, always. Falling back to symbol only when ISIN cannot decide
    means a rename is still caught correctly whenever both sides know the
    ISIN, and the weaker comparison is used only where the stronger one is
    unavailable.
    """
    isin = row.get("isin") or ""
    if isin and isin in by_isin:
        return by_isin[isin], BY_ISIN

    symbol = row.get("symbol") or ""
    if symbol and symbol in by_symbol:
        held = by_symbol[symbol]
        # Both sides carry an ISIN and they disagree: same ticker, different
        # scrip. Not a match at all -- treating it as one would compare two
        # different companies' quantities.
        if isin and held.isin and held.isin != isin:
            return None, BY_ISIN
        return held, BY_SYMBOL if not isin else BY_ISIN

    return None, BY_ISIN if isin else BY_SYMBOL


@dataclass(frozen=True, slots=True)
class Pairing:
    """One book position and the demat row it was paired with, if any."""

    symbol: str
    isin: str
    book_quantity: int
    held: BrokerHolding | None
    basis: str

    @property
    def agrees(self) -> bool:
        return self.held is not None and self.held.quantity == self.book_quantity

    @property
    def weak(self) -> bool:
        """Paired on symbol alone, so a rename could have fooled it."""
        return self.held is not None and self.basis == BY_SYMBOL


def pair(
    book: Sequence[dict],
    demat: Sequence[BrokerHolding],
) -> tuple[list[Pairing], list[BrokerHolding]]:
    """Line the two sides up. Returns pairings and unclaimed demat rows.

    **One matcher, used by both the check and the page.** They previously had
    separate implementations, which meant the table could show a position as
    absent while the findings said the book agreed -- the reader has no way to
    tell which is lying, and both look authoritative.
    """
    by_isin = {h.isin: h for h in demat if h.isin}
    by_symbol = {h.symbol: h for h in demat if h.symbol}
    pairings: list[Pairing] = []
    consumed: set[int] = set()

    for row in book:
        quantity = int(row.get("quantity") or 0)
        if quantity == 0:
            continue
        held, basis = _match(row, by_isin=by_isin, by_symbol=by_symbol)
        if held is not None:
            consumed.add(id(held))
        pairings.append(Pairing(
            symbol=row.get("symbol", "?"),
            isin=row.get("isin") or "",
            book_quantity=quantity,
            held=held,
            basis=basis,
        ))

    unclaimed = [h for h in demat
                 if id(h) not in consumed and h.quantity != 0]
    return pairings, unclaimed


def check_holdings(
    portfolio_id: str,
    *,
    book: Sequence[dict],
    demat: Sequence[BrokerHolding],
) -> Report:
    """Compare book holdings against the broker's.

    `book` rows are decoded `holdings` documents, each needing `symbol` and
    `quantity`; `isin` is used when present and not required.
    """
    report = Report(portfolio_id=portfolio_id)
    pairings, unclaimed = pair(book, demat)

    for pairing in pairings:
        report.checked += 1
        if pairing.held is None:
            report.add("missing_at_broker",
                       f"{pairing.symbol}: book holds "
                       f"{pairing.book_quantity}, the demat has no such "
                       f"position")
        elif not pairing.agrees:
            weak = (" (matched on symbol; neither side gave an ISIN)"
                    if pairing.weak else "")
            report.add("quantity_mismatch",
                       f"{pairing.symbol}: book holds "
                       f"{pairing.book_quantity}, demat holds "
                       f"{pairing.held.quantity}{weak}")

    for held in unclaimed:
        report.checked += 1
        report.add("untracked_at_broker",
                   f"{held.symbol}: demat holds {held.quantity}, the book has "
                   f"no such position")

    return report
