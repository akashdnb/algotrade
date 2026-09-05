"""A portfolio may only reach the broker it is allowed to (design §12).

This file is the safety property; `brokers/wiring.py` is only where it can be
checked. Both directions matter and they fail differently:

* a **live** book given the stub silently simulates orders while the book
  records them as real -- you would believe you were trading and not be;
* a **rehearsal** book given the real client places real orders during what
  everyone involved believes is a dry run.

Neither announces itself. Both look exactly like a successful submission,
which is why this is a construction-time refusal rather than a code review
convention.
"""

from __future__ import annotations

import pytest

from algotrade.brokers.kite import KiteBroker
from algotrade.brokers.stub import StubBroker
from algotrade.brokers.wiring import WiringError, check, client_for, is_live

LIVE = "live_momentum_v1_001"
PAPER = "paper_momentum_v1_001"
SHADOW = "shadow_momentum_v1_001"
REHEARSAL = "paper_momentum_v1_002"


def _kite() -> KiteBroker:
    return KiteBroker(api_key="key", access_token="tok")


# ------------------------------------------------------------- is_live ---

def test_only_the_live_prefix_counts_as_live():
    assert is_live(LIVE)
    for other in (PAPER, SHADOW, REHEARSAL, ""):
        assert not is_live(other), other


def test_a_name_merely_containing_live_is_not_live():
    """`schema.py` constrains ids to `^(paper|live|shadow)_`, so this cannot
    happen today -- but the check must not depend on that holding."""
    assert not is_live("paper_liveish_v1_001")


# --------------------------------------------------------- the refusals ---

def test_a_live_book_may_not_be_given_the_stub():
    """You would believe you were trading and not be."""
    with pytest.raises(WiringError, match="silently simulated"):
        check(LIVE, StubBroker())


@pytest.mark.parametrize("portfolio", [PAPER, SHADOW, REHEARSAL])
def test_a_non_live_book_may_not_be_given_the_real_broker(portfolio):
    """A rehearsal would place real orders."""
    with pytest.raises(WiringError, match="place real orders"):
        check(portfolio, _kite())


def test_an_unrecognised_client_is_refused_rather_than_assumed_safe():
    """An unreviewed implementation is not automatically harmless."""
    class Homemade:
        pass

    with pytest.raises(WiringError, match="neither"):
        check(PAPER, Homemade())


# ------------------------------------------------------- the allowances ---

def test_a_live_book_with_the_real_broker_is_accepted():
    check(LIVE, _kite())


@pytest.mark.parametrize("portfolio", [PAPER, SHADOW, REHEARSAL])
def test_a_non_live_book_with_the_stub_is_accepted(portfolio):
    check(portfolio, StubBroker())


# ---------------------------------------------------------- client_for ---

def test_a_non_live_book_gets_the_stub_even_with_credentials_present():
    """Credentials lying around must not be enough to point a paper book at a
    real exchange."""
    client = client_for(PAPER, api_key="key", access_token="tok")
    assert isinstance(client, StubBroker)


def test_a_live_book_gets_the_real_client():
    assert isinstance(client_for(LIVE, api_key="key", access_token="tok"),
                      KiteBroker)


def test_a_live_book_without_credentials_refuses_rather_than_falling_back():
    """Falling back to the stub would be the worst possible failure: the book
    records real trades while nothing reaches an exchange."""
    with pytest.raises(WiringError, match="refusing to fall back"):
        client_for(LIVE)


def test_a_supplied_stub_is_used_for_a_rehearsal():
    """So a test can arm the fault switch on the instance under test."""
    armed = StubBroker()
    armed.fail_next("margin")
    assert client_for(REHEARSAL, stub=armed) is armed


def test_a_supplied_stub_is_ignored_for_a_live_book():
    """Passing one must not be a way to smuggle the stub onto a live book."""
    with pytest.raises(WiringError):
        client_for(LIVE, stub=StubBroker())


def test_client_for_checks_what_it_returns():
    """The factory does not exempt itself from the rule it exists to enforce."""
    for portfolio in (LIVE, PAPER, SHADOW, REHEARSAL):
        kwargs = {"api_key": "k", "access_token": "t"} if portfolio == LIVE else {}
        check(portfolio, client_for(portfolio, **kwargs))
