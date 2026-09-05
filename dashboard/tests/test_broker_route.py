"""The /broker page renders, and says the right thing when it cannot connect.

The view logic is tested in `test_broker.py`. What is checked here is that the
page exists, is behind auth like every other page, and -- most importantly --
that with no broker configured it says so rather than rendering an empty table
that reads as "you hold nothing".
"""

from __future__ import annotations


def test_the_page_requires_authentication(client):
    assert client.get("/broker").status_code == 401


def test_the_page_renders_with_no_broker_configured(client, auth):
    response = client.get("/broker", auth=auth)
    assert response.status_code == 200
    assert "Broker" in response.text


def test_with_no_token_key_it_names_a_deployment_problem(client, auth):
    """Distinct from 'not authorised today': different remedy entirely."""
    body = client.get("/broker", auth=auth).text
    assert "BROKER_TOKEN_KEY" in body
    assert "Not connected" in body


def test_it_never_claims_agreement_without_having_checked(client, auth):
    """The failure that would matter: a page that cannot reach the broker
    must not render the reassuring case."""
    body = client.get("/broker", auth=auth).text
    assert "matches the demat" not in body


def test_the_page_states_it_cannot_place_an_order(client, auth):
    body = client.get("/broker", auth=auth).text
    assert "cannot place an order" in body


def test_the_nav_links_to_the_broker_page(client, auth):
    assert "/broker" in client.get("/holdings", auth=auth).text
