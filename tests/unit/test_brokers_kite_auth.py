"""Tests for the Kite OAuth handshake (design §5).

The property that matters most is negative: **no secret and no credential ever
leaves this module except as a SHA-256 digest.** The login URL is handed to a
browser and lands in history and logs, so it must carry the key and nothing
else.
"""

from __future__ import annotations

import hashlib

import pytest

from algotrade.brokers import kite_auth
from algotrade.brokers.base import BrokerAuthError, BrokerError


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    def __init__(self, response=None, boom=None):
        self._response = response
        self._boom = boom
        self.posts: list[tuple[str, dict, dict]] = []
        self.deletes: list[tuple[str, dict]] = []

    def post(self, url, data=None, headers=None, timeout=None):
        if self._boom:
            raise self._boom
        self.posts.append((url, data or {}, headers or {}))
        return self._response

    def delete(self, url, params=None, headers=None, timeout=None):
        if self._boom:
            raise self._boom
        self.deletes.append((url, params or {}))
        return self._response


# ------------------------------------------------------------ login URL ---

def test_the_login_url_carries_the_key_and_the_version():
    url = kite_auth.login_url("key123")
    assert url.startswith(kite_auth.LOGIN_URL)
    assert "api_key=key123" in url
    assert "v=3" in url


def test_the_login_url_never_carries_the_secret():
    """It is handed to a browser; it lands in history, logs and referrers."""
    assert "secret" not in kite_auth.login_url("key123").lower()


def test_a_login_url_without_a_key_is_refused():
    with pytest.raises(BrokerError, match="api_key"):
        kite_auth.login_url("")


# ------------------------------------------------------------- checksum ---

def test_the_checksum_is_sha256_of_key_token_secret_in_that_order():
    got = kite_auth.checksum("key", "req", "sec")
    assert got == hashlib.sha256(b"keyreqsec").hexdigest()


def test_the_checksum_changes_with_the_request_token():
    """Otherwise a captured checksum would authorise any exchange."""
    assert kite_auth.checksum("key", "a", "sec") != kite_auth.checksum(
        "key", "b", "sec")


# ------------------------------------------------------------- exchange ---

def _exchange(session, **kwargs):
    args = dict(api_key="key", api_secret="sec", request_token="req")
    args.update(kwargs)
    return kite_auth.exchange(session=session, **args)


def test_a_successful_exchange_returns_only_the_access_token():
    """The payload also carries the user's name, email and broker id. Not
    decoding them is the simplest way to be certain none is ever stored."""
    session = FakeSession(FakeResponse(
        {"status": "success",
         "data": {"access_token": "tok", "user_name": "A Person",
                  "email": "someone@example.com"}}))
    assert _exchange(session) == "tok"


def test_the_secret_is_sent_only_as_a_checksum():
    session = FakeSession(FakeResponse(
        {"status": "success", "data": {"access_token": "tok"}}))
    _exchange(session)
    _, data, _ = session.posts[0]
    assert "sec" not in data.values()
    assert data["checksum"] == kite_auth.checksum("key", "req", "sec")


def test_a_refused_exchange_is_an_auth_error_naming_the_likely_cause():
    """A reused or stale request token is by far the most common failure, and
    a refreshed callback URL is enough to cause it."""
    session = FakeSession(FakeResponse(
        {"status": "error", "message": "Invalid `request_token`."},
        status_code=400))
    with pytest.raises(BrokerAuthError, match="single-use"):
        _exchange(session)


def test_a_success_without_a_token_is_an_error_not_an_empty_string():
    """An empty token stored as a live session would fail later, at the
    broker, for no visible reason."""
    session = FakeSession(FakeResponse({"status": "success", "data": {}}))
    with pytest.raises(BrokerError, match="no access_token"):
        _exchange(session)


def test_a_non_json_response_is_refused():
    with pytest.raises(BrokerError, match="not JSON"):
        _exchange(FakeSession(FakeResponse(None)))


def test_a_network_failure_is_a_broker_error_not_an_auth_error():
    """It says nothing about the session; a login button would not help."""
    import requests
    session = FakeSession(boom=requests.RequestException("dns"))
    with pytest.raises(BrokerError) as caught:
        _exchange(session)
    assert not isinstance(caught.value, BrokerAuthError)


def test_missing_inputs_are_refused_before_any_request():
    session = FakeSession()
    with pytest.raises(BrokerError, match="needs api_key"):
        _exchange(session, request_token="")
    assert session.posts == []


# ----------------------------------------------------------- invalidate ---

def test_a_confirmed_logout_reports_true():
    session = FakeSession(FakeResponse({"status": "success"}))
    assert kite_auth.invalidate(api_key="key", access_token="tok",
                                session=session)


def test_a_refused_logout_reports_false_rather_than_raising():
    """Logout is best-effort. A failure must not stop us discarding the local
    row -- a token we have forgotten is unusable by us regardless."""
    session = FakeSession(FakeResponse({}, status_code=500))
    assert not kite_auth.invalidate(api_key="key", access_token="tok",
                                    session=session)


def test_a_network_failure_during_logout_does_not_raise():
    import requests
    session = FakeSession(boom=requests.RequestException("dns"))
    assert not kite_auth.invalidate(api_key="key", access_token="tok",
                                    session=session)
