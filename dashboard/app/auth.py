"""HTTP Basic Auth, plus CSRF protection for the routes that write.

The deployment URL is guessable and indexed, and every page shows holdings,
NAV and broker order ids. Basic Auth over the host's TLS is proportionate for a
single-user dashboard; what it is *not* is CSRF protection.

**Why CSRF matters here specifically.** With Basic Auth the browser attaches
credentials to every request to the origin automatically, including one caused
by a form on somebody else's page. Once this dashboard can record executions,
a cross-site POST would write to the book using your credentials. Cookies would
give us `SameSite`; Basic Auth gives us nothing, so the token below is not
optional ceremony.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Annotated
from urllib.parse import urlparse

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.settings import Settings, get_settings

_scheme = HTTPBasic(auto_error=False)

#: Signing key for CSRF tokens. Regenerated per container, which means a form
#: rendered before a redeploy is rejected after it -- correct, and far simpler
#: than persisting a secret the dashboard has no other use for.
_CSRF_KEY = secrets.token_bytes(32)


def _matches(given: str, expected: str) -> bool:
    """Constant-time, so a wrong password cannot be found a byte at a time."""
    return hmac.compare_digest(given.encode(), expected.encode())


def require_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_scheme)] = None,
) -> str:
    settings = get_settings()
    unauthorised = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authorised",
        headers={"WWW-Authenticate": 'Basic realm="algotrade"'},
    )
    if credentials is None:
        raise unauthorised

    # Both compared unconditionally: short-circuiting on the username would
    # leak whether a username is valid.
    user_ok = _matches(credentials.username, settings.auth_user)
    password_ok = _matches(credentials.password, settings.auth_password)
    if not (user_ok and password_ok):
        raise unauthorised

    request.state.user = credentials.username
    return credentials.username


# ----------------------------------------------------------------- CSRF -----


def issue_csrf_token() -> str:
    """A token to embed in a form that will cause a write."""
    nonce = secrets.token_urlsafe(24)
    signature = hmac.new(_CSRF_KEY, nonce.encode(), "sha256").hexdigest()[:32]
    return f"{nonce}.{signature}"


def _token_is_valid(token: str) -> bool:
    nonce, _, signature = token.partition(".")
    if not nonce or not signature:
        return False
    expected = hmac.new(_CSRF_KEY, nonce.encode(), "sha256").hexdigest()[:32]
    return hmac.compare_digest(signature, expected)


def _same_origin(request: Request) -> bool:
    """Defence in depth: the browser sets Origin on cross-site form posts.

    Checked in addition to the token, not instead of it -- Origin is absent on
    some legitimate requests, so it can support a decision but not make one.
    """
    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        return True                      # absent: fall back to the token alone
    host = urlparse(origin).netloc
    return bool(host) and host == request.headers.get("host")


async def require_csrf(request: Request) -> None:
    """Dependency for any route that mutates. Reads the form once and caches
    it on the request so the handler does not consume the body a second time.
    """
    if not _same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Cross-origin request refused")

    form = await request.form()
    request.state.form = form
    token = str(form.get("csrf_token", ""))
    if not _token_is_valid(token):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This form has expired. Reload the page and try again.",
        )


def require_writes_enabled(settings: Annotated[Settings, Depends(get_settings)]) -> None:
    """Refuse the write path unless it was switched on deliberately."""
    if not settings.allow_writes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This dashboard is running read-only (DASHBOARD_ALLOW_WRITES is not set).",
        )
