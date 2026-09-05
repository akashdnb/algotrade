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

from algotrade.platform import clock
from algotrade.brokers.base import (
    BrokerAuthError,
    BrokerError,
    BrokerFunds,
    BrokerHolding,
)
from algotrade.services import demat as demat_service
from algotrade.services.invariants import Finding

#: The finding kind that is informational rather than a defect.
UNTRACKED = "untracked_at_broker"

#: What the page needs to say about the broker session, in priority order.
NO_KEY = "no_key"
NO_SESSION = "no_session"
#: Stored and unexpired, but the broker refused it anyway -- a master logout
#: from Kite's web terminal does this, and no expiry margin can predict it.
REJECTED = "rejected"
LIVE = "live"

#: States whose remedy is the same single action: authorise again.
NEEDS_LOGIN = frozenset({NO_SESSION, REJECTED})


@dataclass(frozen=True, slots=True)
class SessionState:
    """Why the page can or cannot talk to the broker."""

    state: str
    expires_at: datetime | None = None
    message: str = ""

    @property
    def usable(self) -> bool:
        return self.state == LIVE

    @property
    def expires_label(self) -> str:
        """When the session dies, as a clock time in IST.

        Not "x ago": the relative formatter describes the past, so a timestamp
        six hours in the FUTURE rendered as "just now" -- which reads as a
        session about to expire when it is the opposite.
        """
        if self.expires_at is None:
            return ""
        return self.expires_at.astimezone(clock.IST).strftime("%H:%M IST")

    @property
    def needs_login(self) -> bool:
        """Whether the page should offer to authorise.

        A rejected session and an absent one are different facts with one
        remedy. Collapsing them would lose the explanation; keeping them apart
        without this would make the template ask the same question twice.
        """
        return self.state in NEEDS_LOGIN


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
    #: Paired on symbol alone, so a rename could have fooled it.
    weak: bool = False

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
    #: Whether this book is *supposed* to match the demat (§11).
    comparable: bool = True

    @property
    def agrees(self) -> bool:
        """True only if we actually checked and found nothing that matters."""
        return (self.comparable and bool(self.rows)
                and not self.disagreements)

    @property
    def disagreements(self) -> list[Finding]:
        """Findings that mean something went wrong.

        For a paper book, none: it holds simulated positions and the demat
        holds real ones, so every line differs, all of it correctly, and none
        of it means anything. Reporting 38 disagreements against a book that
        was never expected to match is how a check teaches you to ignore it --
        the exact failure `services/demat.py` avoids on series changes.

        And **`untracked_at_broker` is never a disagreement**, even live. The
        system manages the momentum sleeve; the same demat also holds the
        NIFTYBEES half, held manually and never traded here (PROJECT.md §11o),
        and whatever else the account owns. A position the book does not know
        about is the normal state of the account, not a defect.
        """
        if not self.comparable:
            return []
        return [f for f in self.findings if f.check != UNTRACKED]

    @property
    def unmanaged(self) -> list[Finding]:
        """Demat positions this system does not manage. Informational."""
        return [f for f in self.findings if f.check == UNTRACKED]

    @property
    def demat_rows(self) -> list[HoldingRow]:
        """What the account actually holds, with no book column.

        For a non-comparable book the comparison is meaningless -- the page
        says so -- and then rendering it anyway contradicts the sentence above
        it. A paper position showing "Demat —, absent" is precisely the
        comparison just disclaimed, and "absent" reads as a discrepancy.

        So the table becomes a plain listing of the demat: still useful, and
        it no longer asserts a difference between two unrelated things.
        """
        return [r for r in self.rows if r.demat_quantity]


def comparable(mode: str) -> bool:
    """Only a live book is expected to match the demat.

    Paper and shadow are simulations; the demat is the real account. Comparing
    them is not a weaker check, it is a different question with no answer.
    """
    return mode == "live"


def align(
    book: list[dict],
    demat: list[BrokerHolding],
) -> list[HoldingRow]:
    """One row per position on either side.

    **Pairing is delegated to `services/demat.pair`**, the same function the
    findings come from. When this module had its own matcher the table could
    show a position as absent while the findings said the book agreed — two
    authoritative-looking claims, no way for a reader to tell which was wrong.
    """
    pairings, unclaimed = demat_service.pair(book, demat)

    rows = [
        HoldingRow(
            symbol=p.symbol,
            isin=p.isin,
            book_quantity=p.book_quantity,
            demat_quantity=p.held.quantity if p.held else None,
            average_price=p.held.average_price if p.held else None,
            weak=p.weak,
        )
        for p in pairings
    ]
    rows.extend(
        HoldingRow(symbol=held.symbol, isin=held.isin, book_quantity=0,
                   demat_quantity=held.quantity,
                   average_price=held.average_price)
        for held in unclaimed
    )
    return sorted(rows, key=lambda r: r.symbol)


def unavailable(session: SessionState, exc: BrokerError) -> View:
    """A broker that would not answer is reported, never rendered as zero.

    An empty demat and an unreachable broker look identical in a table and mean
    opposite things -- one says you hold nothing, the other says we do not
    know.

    A rejected session is downgraded to `REJECTED` rather than left reading as
    live. Otherwise the page would show "session until 05:58" beside an error
    saying the session was refused, and the more confident of the two claims is
    the wrong one.
    """
    if isinstance(exc, BrokerAuthError):
        session = SessionState(REJECTED, message=str(exc))
    return View(session=session, error=str(exc))
