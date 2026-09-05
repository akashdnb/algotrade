"""The login, callback and logout routes (design §5).

What is checked here is the routing and the refusals, not the handshake --
`tests/unit/test_brokers_kite_auth.py` covers that without a network.

The redirects are all 303. A 302 after the POST logout would let some clients
re-issue it as a POST on refresh.
"""

from __future__ import annotations

import pytest


@pytest.fixture
def configured(monkeypatch):
    """A deployment with every broker setting present."""
    import base64

    from algotrade.brokers import tokens
    monkeypatch.setenv("BROKER_NAME", "zerodha")
    monkeypatch.setenv("BROKER_API_KEY", "key123")
    monkeypatch.setenv("BROKER_API_SECRET", "secret123")
    monkeypatch.setenv("BROKER_TOKEN_KEY",
                       base64.b64encode(tokens.generate_key()).decode())
    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------- login ---

def test_login_requires_authentication(client):
    assert client.get("/broker/login", follow_redirects=False).status_code == 401


def test_login_sends_the_browser_to_zerodha(client, auth, configured):
    response = client.get("/broker/login", auth=auth, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("https://kite.zerodha.com/connect/login")
    assert "api_key=key123" in location


def test_the_login_redirect_never_carries_the_secret(client, auth, configured):
    """It lands in browser history, logs and referrer headers."""
    location = client.get("/broker/login", auth=auth,
                          follow_redirects=False).headers["location"]
    assert "secret123" not in location


def test_login_without_credentials_goes_back_rather_than_to_the_broker(
        client, auth):
    response = client.get("/broker/login", auth=auth, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/broker")


# ------------------------------------------------------------- callback ---

def test_a_cancelled_login_returns_with_a_message(client, auth, configured):
    response = client.get("/broker/callback?status=cancelled", auth=auth,
                          follow_redirects=False)
    assert response.status_code == 303
    assert "message=" in response.headers["location"]
    assert "cancelled" in response.headers["location"]


def test_a_callback_without_a_request_token_says_so(client, auth, configured):
    response = client.get("/broker/callback", auth=auth,
                          follow_redirects=False)
    assert "request+token" in response.headers["location"].replace("%20", "+")


def test_a_callback_with_no_credentials_configured_says_so(client, auth):
    response = client.get("/broker/callback?request_token=abc", auth=auth,
                          follow_redirects=False)
    assert "not+configured" in response.headers["location"].replace("%20", "+")


def test_the_callback_never_echoes_the_request_token_back_into_the_url(
        client, auth, configured):
    """It is a credential, however short-lived. Leaving it in the address bar
    puts it in history and in any referrer that follows."""
    response = client.get("/broker/callback?status=cancelled&request_token=abc",
                          auth=auth, follow_redirects=False)
    assert "abc" not in response.headers["location"]


# --------------------------------------------------------------- logout ---

def test_logout_requires_authentication(client):
    assert client.post("/broker/logout",
                       follow_redirects=False).status_code == 401


def test_logout_is_not_reachable_by_get(client, auth):
    """A GET that ends a session could be triggered by an image tag."""
    assert client.get("/broker/logout", auth=auth,
                      follow_redirects=False).status_code == 405


def test_logout_with_no_session_still_redirects_cleanly(client, auth,
                                                        configured):
    response = client.post("/broker/logout", auth=auth,
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/broker")


# ----------------------------------------------------------------- page ---

def test_the_page_offers_a_login_when_fully_configured(client, auth,
                                                       configured):
    body = client.get("/broker", auth=auth).text
    assert "Log in with Zerodha" in body


def test_a_missing_secret_names_the_settings_instead_of_a_dead_login(
        client, auth, monkeypatch):
    """Key and token key present, secret absent. The handshake would fail at
    its last step, so offering the button would be a trap."""
    import base64

    from algotrade.brokers import tokens
    monkeypatch.setenv("BROKER_API_KEY", "key123")
    monkeypatch.setenv("BROKER_TOKEN_KEY",
                       base64.b64encode(tokens.generate_key()).decode())
    monkeypatch.delenv("BROKER_API_SECRET", raising=False)
    from app.settings import get_settings
    get_settings.cache_clear()

    body = client.get("/broker", auth=auth).text
    assert "Log in with Zerodha" not in body
    assert "BROKER_API_SECRET" in body


def test_with_nothing_configured_the_page_names_the_token_key(client, auth):
    body = client.get("/broker", auth=auth).text
    assert "Log in with Zerodha" not in body
    assert "BROKER_TOKEN_KEY" in body
