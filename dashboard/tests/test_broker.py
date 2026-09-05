"""Tests for the broker section's view assembly (design §11, §15 step 1).

Pure functions, so every case that matters is testable without a server or a
network. The two that matter most:

* an unreachable broker must not render as an empty demat -- they look
  identical in a table and mean opposite things;
* a series move must not appear as two wrong rows.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from algotrade.brokers.base import BrokerAuthError, BrokerError, BrokerHolding

from app import broker

HFCL = "INE548A01028"
RELIANCE = "INE002A01018"


def _held(symbol="HFCL", quantity=30, isin=HFCL):
    return BrokerHolding(symbol=symbol, isin=isin, quantity=quantity,
                         average_price=Decimal("224.17"))


def _book(symbol="HFCL", quantity=30, isin=HFCL):
    return {"symbol": symbol, "isin": isin, "quantity": quantity}


# -------------------------------------------------------- session state ---

def test_no_token_key_is_reported_as_a_deployment_problem():
    state = broker.session_state(token_key_configured=False, ciphertext=None,
                                 expires_at=None)
    assert state.state == broker.NO_KEY
    assert not state.usable
    assert "deployment setting" in state.message


def test_no_session_is_reported_as_the_ordinary_morning_state():
    """Not an error. Kite drops every token at 06:00 IST by regulation."""
    state = broker.session_state(token_key_configured=True, ciphertext=None,
                                 expires_at=None)
    assert state.state == broker.NO_SESSION
    assert "06:00 IST" in state.message


def test_a_stored_session_is_usable_and_shows_its_expiry():
    expires = datetime(2026, 10, 2, 6, 0)
    state = broker.session_state(token_key_configured=True, ciphertext="abc",
                                 expires_at=expires)
    assert state.usable
    assert state.expires_at == expires


def test_a_missing_key_outranks_a_stored_session():
    """Ciphertext without a key is undecryptable; saying "live" would lie."""
    state = broker.session_state(token_key_configured=False,
                                 ciphertext="abc", expires_at=None)
    assert state.state == broker.NO_KEY


# ---------------------------------------------------------------- align ---

def test_a_matching_position_produces_one_agreeing_row():
    rows = broker.align([_book()], [_held()])
    assert len(rows) == 1
    assert rows[0].agrees
    assert rows[0].difference == 0


def test_a_quantity_difference_is_signed_from_the_books_point_of_view():
    rows = broker.align([_book(quantity=30)], [_held(quantity=28)])
    assert rows[0].difference == -2
    assert not rows[0].agrees


def test_a_position_only_the_broker_has_appears_as_a_row():
    rows = broker.align([], [_held()])
    assert rows[0].only_at_broker
    assert rows[0].book_quantity == 0


def test_a_position_only_the_book_has_appears_with_no_demat_quantity():
    rows = broker.align([_book()], [])
    assert rows[0].only_in_book
    assert rows[0].demat_quantity is None
    assert rows[0].difference is None


def test_a_series_move_is_one_agreeing_row_not_two_wrong_ones():
    """HFCL EQ->BE renames the scrip; the position never moved."""
    rows = broker.align([_book(symbol="HFCL")],
                        [_held(symbol="HFCL-BE")])
    assert len(rows) == 1
    assert rows[0].agrees


def test_rows_are_sorted_by_symbol():
    rows = broker.align([_book(symbol="ZEEL", isin=RELIANCE),
                         _book(symbol="ACC")], [])
    assert [r.symbol for r in rows] == ["ACC", "ZEEL"]


def test_a_closed_book_position_is_not_listed():
    assert broker.align([_book(quantity=0)], []) == []


def test_a_zero_quantity_demat_row_is_not_listed():
    """Kite reports sold-out holdings at zero on settlement day."""
    assert broker.align([], [_held(quantity=0)]) == []


def test_a_book_row_without_an_isin_still_appears():
    """It cannot be matched, but hiding it would make the table claim a
    completeness it does not have."""
    rows = broker.align([_book(isin="")], [])
    assert len(rows) == 1
    assert rows[0].demat_quantity is None


# ----------------------------------------------------------------- view ---

def test_an_unreachable_broker_is_an_error_not_an_empty_table():
    """An empty demat and an unreachable broker look identical in a table and
    mean opposite things: 'you hold nothing' versus 'we do not know'."""
    state = broker.session_state(token_key_configured=True, ciphertext="abc",
                                 expires_at=None)
    view = broker.unavailable(state, BrokerError("connection reset"))
    assert view.error
    assert view.rows == []
    assert not view.agrees


def test_a_network_failure_leaves_the_session_reading_as_live():
    """The session is fine; the network was not. Offering a login button would
    send the user to do something that cannot help."""
    state = broker.session_state(token_key_configured=True, ciphertext="abc",
                                 expires_at=datetime(2026, 10, 2, 5, 58))
    view = broker.unavailable(state, BrokerError("connection reset"))
    assert view.session.usable
    assert not view.session.needs_login


def test_a_rejected_session_is_downgraded_rather_than_left_reading_live():
    """Otherwise the page shows 'session until 05:58' beside an error saying
    the session was refused, and the more confident claim is the wrong one."""
    state = broker.session_state(token_key_configured=True, ciphertext="abc",
                                 expires_at=datetime(2026, 10, 2, 5, 58))
    view = broker.unavailable(state, BrokerAuthError("master logout"))
    assert view.session.state == broker.REJECTED
    assert not view.session.usable
    assert view.session.needs_login
    assert view.session.expires_at is None


def test_an_absent_session_also_asks_for_a_login():
    """Different fact, same single remedy."""
    state = broker.session_state(token_key_configured=True, ciphertext=None,
                                 expires_at=None)
    assert state.needs_login


def test_a_missing_token_key_does_not_ask_for_a_login():
    """Authorising cannot fix a setting the deployment never provided."""
    state = broker.session_state(token_key_configured=False, ciphertext=None,
                                 expires_at=None)
    assert not state.needs_login


def test_a_view_with_no_rows_does_not_claim_agreement():
    """Vacuous truth would read as 'checked, all good'."""
    view = broker.View(session=broker.session_state(
        token_key_configured=True, ciphertext="abc", expires_at=None))
    assert not view.agrees


def test_a_view_with_rows_and_no_findings_agrees():
    view = broker.View(
        session=broker.session_state(token_key_configured=True,
                                     ciphertext="abc", expires_at=None),
        rows=broker.align([_book()], [_held()]),
    )
    assert view.agrees
