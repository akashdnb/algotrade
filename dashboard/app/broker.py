"""Assembling the broker section's view, without any of it being a route.

The route decides nothing. Everything here is a pure function of what the
broker and the book returned, so the interesting cases -- no session, an
expired session, a demat that disagrees -- are testable without a server and
without a network.

**Nothing in this module writes, and nothing places an order.** The read-only
section is design §15 step 1; submission is step 4 and lives elsewhere, on a
host with a registered static IP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from algotrade.brokers.base import BrokerError, BrokerFunds, BrokerHolding
from algotrade.services.invariants import Finding

#: What the page needs to say about the broker session, in priority order.
NO_KEY = "no_key"
NO_SESSION = "no_session"
LIVE = "live"


@dataclass(frozen=True, slots=True)
class SessionState:
    """Why the page can or cannot talk to the broker."""

    state: str
    expires_at: datetime | None = None
    message: str = ""

    @property
    def usable(self) -> bool:
        return self.state == LIVE


def session_state(
    *,
    token_key_configured: bool,
    ciphertext: str | None,
    expires_at: datetime | None,
) -> SessionState:
    """Decide what the page can do, before any network call is attempted.

    Distinguishes "no key configured" from "not authorised today" because the
    remedies are different and unrelated: one is a deployment problem, the
    other is a thing you do every morning.
    """
    if not token_key_configured:
        return SessionState(
            NO_KEY,
            message="BROKER_TOKEN_KEY is not set, so a session cannot be "
                    "stored. This is a deployment setting, not something to "
                    "fix here.",
        )
    if not ciphertext:
        return SessionState(
            NO_SESSION,
            message="Not authorised. Kite invalidates every access token at "
                    "06:00 IST, so this is expected each morning.",
        )
    return SessionState(LIVE, expires_at=expires_at)


@dataclass(frozen=True, slots=True)
class HoldingRow:
    """One line of the comparison table, book beside broker."""

    symbol: str
    isin: str
    book_quantity: int
    demat_quantity: int | None
    average_price: Decimal | None

    @property
    def agrees(self) -> bool:
        return self.demat_quantity == self.book_quantity

    @property
    def difference(self) -> int | None:
        if self.demat_quantity is None:
            return None
        return self.demat_quantity - self.book_quantity

    @property
    def only_at_broker(self) -> bool:
        return self.book_quantity == 0 and bool(self.demat_quantity)

    @property
    def only_in_book(self) -> bool:
        return self.demat_quantity is None and self.book_quantity > 0


@dataclass(frozen=True, slots=True)
class View:
    session: SessionState
    rows: list[HoldingRow] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    funds: BrokerFunds | None = None
    error: str = ""

    @property
    def agrees(self) -> bool:
        """True only if we actually checked and found nothing."""
        return bool(self.rows) and not self.findings


def align(
    book: list[dict],
    demat: list[BrokerHolding],
) -> list[HoldingRow]:
    """One row per position on either side, matched on ISIN.

    Matched on ISIN rather than symbol for the same reason `services/demat.py`
    is: a series move renames the scrip without moving the position, and symbol
    matching would show it as two rows that each look wrong.
    """
    theirs = {h.isin: h for h in demat if h.isin}
    rows: list[HoldingRow] = []
    seen: set[str] = set()

    for row in book:
        quantity = int(row.get("quantity") or 0)
        if quantity == 0:
            continue
        isin = row.get("isin") or ""
        held = theirs.get(isin)
        if isin:
            seen.add(isin)
        rows.append(HoldingRow(
            symbol=row.get("symbol", "?"),
            isin=isin,
            book_quantity=quantity,
            demat_quantity=held.quantity if held else None,
            average_price=held.average_price if held else None,
        ))

    for isin, held in theirs.items():
        if isin in seen or not held.quantity:
            continue
        rows.append(HoldingRow(
            symbol=held.symbol, isin=isin, book_quantity=0,
            demat_quantity=held.quantity, average_price=held.average_price,
        ))

    return sorted(rows, key=lambda r: r.symbol)


def unavailable(session: SessionState, exc: BrokerError) -> View:
    """A broker that would not answer is reported, never rendered as zero.

    An empty demat and an unreachable broker look identical in a table and mean
    opposite things -- one says you hold nothing, the other says we do not
    know.
    """
    return View(session=session, error=str(exc))
