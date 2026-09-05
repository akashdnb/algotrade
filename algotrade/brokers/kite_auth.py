"""Kite's OAuth handshake: login URL, token exchange, and logout.

The flow, and why it is shaped this way:

1. Send the browser to `login_url()`. The user authenticates **on Zerodha's
   own page**, with their own 2FA.
2. Zerodha redirects back to the app's registered URL carrying a
   `request_token`.
3. `exchange()` trades that, plus a checksum proving we hold the API secret,
   for an `access_token`.

**We never see the password or the TOTP seed.** That is the whole reason to use
the redirect flow rather than collecting credentials ourselves: Zerodha's
stated position is that login should not be automated, and storing a password
and TOTP seed on a server is exactly the thing that warning is about.

The `request_token` is single-use and short-lived, which is what makes step 2
survivable as a GET.
"""

from __future__ import annotations

import hashlib
from typing import Any, Callable

import requests

from algotrade.brokers.base import BrokerAuthError, BrokerError
from algotrade.brokers.kite import BASE_URL, API_VERSION

LOGIN_URL: str = "https://kite.zerodha.com/connect/login"


def login_url(api_key: str) -> str:
    """Where to send the browser. No secret appears in it."""
    if not api_key:
        raise BrokerError("cannot build a login URL without an api_key")
    return f"{LOGIN_URL}?v={API_VERSION}&api_key={api_key}"


def checksum(api_key: str, request_token: str, api_secret: str) -> str:
    """SHA-256 of `api_key + request_token + api_secret`.

    Proves we hold the secret without transmitting it.
    """
    payload = f"{api_key}{request_token}{api_secret}".encode()
    return hashlib.sha256(payload).hexdigest()


def exchange(
    *,
    api_key: str,
    api_secret: str,
    request_token: str,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> str:
    """Trade a `request_token` for an `access_token`.

    Returns the token itself rather than the whole payload: the rest of the
    response carries the user's name, email and broker id, and this system has
    no use for any of it. Not decoding it is the simplest way to be sure it is
    never stored.
    """
    if not (api_key and api_secret and request_token):
        raise BrokerError(
            "token exchange needs api_key, api_secret and request_token")

    http = session or requests.Session()
    try:
        response = http.post(
            f"{BASE_URL}/session/token",
            data={
                "api_key": api_key,
                "request_token": request_token,
                "checksum": checksum(api_key, request_token, api_secret),
            },
            headers={"X-Kite-Version": API_VERSION},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise BrokerError(f"token exchange failed: {exc}") from exc

    try:
        payload: dict[str, Any] = response.json()
    except ValueError as exc:
        raise BrokerError("token exchange: response was not JSON") from exc

    if response.status_code >= 400 or payload.get("status") != "success":
        message = payload.get("message") or f"HTTP {response.status_code}"
        # A stale or reused request_token lands here, and it is by far the most
        # likely failure -- a refreshed callback URL is enough to cause it.
        raise BrokerAuthError(
            f"token exchange refused: {message}. A request token is "
            "single-use and short-lived; start the login again."
        )

    token = (payload.get("data") or {}).get("access_token")
    if not token:
        raise BrokerError("token exchange succeeded but carried no access_token")
    return token


def invalidate(
    *,
    api_key: str,
    access_token: str,
    session: requests.Session | None = None,
    timeout: float = 10.0,
) -> bool:
    """End the session at Kite. Returns whether they confirmed it.

    Deleting our stored row without calling this leaves the token alive on
    their side, which is the opposite of what "log out" is understood to mean.

    Never raises: logout is a best-effort courtesy, and a failure here must not
    stop us discarding the local row. A token we have forgotten is unusable by
    us whatever Kite thinks.
    """
    http = session or requests.Session()
    try:
        response = http.delete(
            f"{BASE_URL}/session/token",
            params={"api_key": api_key, "access_token": access_token},
            headers={"X-Kite-Version": API_VERSION},
            timeout=timeout,
        )
        return response.status_code < 400
    except requests.RequestException:
        return False
