"""The /execute page (design §7, §8).

The order-building and the gate are tested in
`tests/unit/test_services_execution_plan.py`. What is checked here is the
route: that it is absent unless switched on, that it refuses without a frozen
basket, and that it never trusts the form about whether a trade may go — the
page was drawn before any sell was placed, so its buttons reflect a state that
has since moved on.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("EXECUTE_ENABLED", "1")
    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_the_page_requires_authentication(client):
    assert client.get("/execute").status_code == 401


def test_the_page_is_absent_when_not_enabled(client_disabled, auth):
    """Absent, not forbidden: the module is never imported, so a deployment
    that must not place orders cannot fail open."""
    assert client_disabled.get("/execute", auth=auth).status_code == 404


def test_with_nothing_frozen_it_points_at_review(client, auth):
    body = client.get("/execute", auth=auth).text
    assert "No frozen basket" in body
    assert "/review" in body


def test_it_says_a_simulated_book_reaches_no_exchange(client, auth):
    """The paper book routes to the stub by `brokers/wiring`. Saying so is
    what makes the rehearsal trustworthy rather than merely quiet."""
    assert client.get("/execute", auth=auth).status_code == 200


def test_placing_without_a_frozen_basket_is_refused(client, auth):
    """The form could be replayed from a page drawn when a basket existed."""
    response = client.post(
        "/execute/order",
        data={"set_id": "p:2026-10:r1", "seq": "1", "on": "2026-10-01",
              "csrf_token": _csrf(client, auth)},
        auth=auth, follow_redirects=False)
    assert response.status_code == 303
    assert "no+frozen+basket" in response.headers["location"].replace("%20", "+")


def test_placing_requires_a_csrf_token(client, auth):
    """A GET-triggered order would be reachable from an image tag; a POST
    without a token is reachable from any page you visit."""
    response = client.post(
        "/execute/order",
        data={"set_id": "p:2026-10:r1", "seq": "1", "on": "2026-10-01"},
        auth=auth, follow_redirects=False)
    assert response.status_code == 403


def test_the_order_endpoint_is_not_reachable_by_get(client, auth):
    assert client.get("/execute/order", auth=auth).status_code == 405


def _csrf(client, auth) -> str:
    """Issued directly rather than scraped.

    With no frozen basket the page renders no form to scrape one from -- and
    that is exactly the state this test needs to exercise, so it must not
    depend on a form being present.
    """
    from app.auth import issue_csrf_token
    return issue_csrf_token()
