"""The /review page (design §6, §15 step 2).

The assembly is tested in `tests/unit/test_services_review.py`; what is checked
here is that the page exists, is behind auth, never reaches the network for a
portfolio with nothing to execute, and states plainly that it cannot trade.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """The page is OFF by default and off on Vercel -- see app/settings.py.
    These tests are about the page, so they turn it on."""
    monkeypatch.setenv("REVIEW_ENABLED", "1")
    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    """A page test must never depend on Yahoo being up.

    Left unpatched, a review page for a portfolio WITH open items would make
    thirty live requests inside the suite -- slow, flaky, and rude.
    """
    from algotrade.io import feed

    def refuse(tickers, *, on, **kwargs):
        raise AssertionError(f"unexpected network call for {list(tickers)}")

    monkeypatch.setattr(feed, "opens", refuse)


def test_the_page_requires_authentication(client):
    assert client.get("/review").status_code == 401


def test_with_nothing_to_execute_it_says_so_and_fetches_no_prices(client, auth):
    """The autouse fixture turns any request into a failure, so reaching 200
    proves the empty path costs nothing."""
    response = client.get("/review", auth=auth)
    assert response.status_code == 200
    assert "Nothing to execute" in response.text


def test_the_page_states_it_places_no_orders(client, auth):
    body = client.get("/review", auth=auth).text
    assert "places no orders" in body


def test_the_nav_links_to_review(client, auth):
    assert "/review" in client.get("/holdings", auth=auth).text


def test_an_unparseable_date_falls_back_to_today_rather_than_failing(
        client, auth):
    """A hand-edited URL should not 500 a page whose whole job is to be read
    carefully before trading."""
    assert client.get("/review?on=not-a-date", auth=auth).status_code == 200


def test_the_page_is_absent_when_not_enabled(client_disabled, auth):
    """Absent, not forbidden. The module is never imported, so it cannot fail
    at startup on a deploy that lacks its dependencies or its config files."""
    assert client_disabled.get("/review", auth=auth).status_code == 404


def test_the_nav_omits_review_when_not_enabled(client_disabled, auth):
    assert "/review" not in client_disabled.get("/holdings", auth=auth).text
