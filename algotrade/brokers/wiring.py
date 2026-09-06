"""Which broker a portfolio is allowed to talk to.

One decision, made once, at construction. The alternative -- deciding inside
the submission path with `if portfolio_id.startswith("live")` -- puts untested
routing logic on the money path, where a single typo sends rehearsal orders to
a real exchange or live orders into a fake one. Neither failure announces
itself: both look like a successful submission.

So the mapping is a function with two rules and no middle ground:

* a **live** book gets the real client, never the stub;
* everything else gets the stub, never the real client.

`tests/unit/test_brokers_wiring.py` asserts both directions. That test is the
actual safety property; this module is just where it can be checked.

Constructing the wrong pairing raises. It does not warn, and it does not fall
back to the safer option -- a caller that asked for the wrong thing has a bug,
and continuing with something it did not ask for hides it.
"""

from __future__ import annotations

from algotrade.brokers.base import BrokerClient
from algotrade.brokers.kite import KiteBroker
from algotrade.brokers.stub import StubBroker

#: Portfolio id prefix that may reach a real exchange. Exactly one.
LIVE_PREFIX: str = "live_"


class WiringError(RuntimeError):
    """A portfolio was paired with a broker it must never use."""


def is_live(portfolio_id: str) -> bool:
    """Whether this book trades real money.

    Read from the id, which `schema.py` constrains to
    `^(paper|live|shadow)_...`, so the prefix cannot be an arbitrary string.
    """
    return portfolio_id.startswith(LIVE_PREFIX)


def check(portfolio_id: str, client: BrokerClient) -> None:
    """Raise unless this client is the one this portfolio may use.

    Called at construction and again before submission, because the two are
    separated by a human reading a screen -- and the second check costs
    nothing next to what it prevents.
    """
    live_book = is_live(portfolio_id)
    live_client = isinstance(client, KiteBroker)
    stub_client = isinstance(client, StubBroker)

    if live_book and stub_client:
        raise WiringError(
            f"{portfolio_id} is a live book and would have been given the "
            "stub: orders would be silently simulated while the book recorded "
            "them as real"
        )
    if not live_book and live_client:
        raise WiringError(
            f"{portfolio_id} is not a live book and would have been given the "
            "real broker: a rehearsal would place real orders"
        )
    if not (live_client or stub_client):
        # An unrecognised implementation is not automatically safe. Say so
        # rather than let an unreviewed client onto the money path.
        raise WiringError(
            f"{portfolio_id}: {type(client).__name__} is neither the live "
            "client nor the stub, so it cannot be checked"
        )


def client_for(
    portfolio_id: str,
    *,
    api_key: str = "",
    access_token: str = "",
    stub: StubBroker | None = None,
) -> BrokerClient:
    """The one client this portfolio may use.

    A live book needs credentials and gets `KiteBroker`; anything else gets the
    stub, and supplying credentials for it changes nothing.
    """
    if is_live(portfolio_id):
        if not (api_key and access_token):
            raise WiringError(
                f"{portfolio_id} is a live book and needs both an api_key and "
                "an access_token; refusing to fall back to the stub"
            )
        client: BrokerClient = KiteBroker(api_key=api_key,
                                          access_token=access_token)
    else:
        client = stub if stub is not None else StubBroker()

    check(portfolio_id, client)
    return client
