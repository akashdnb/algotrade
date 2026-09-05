"""Encryption and expiry for the broker session token.

**Encrypted, not hashed.** Kite's `Authorization: token api_key:access_token`
header needs the plaintext, so a one-way hash would store something that can
never be used. Hashing is for values you only need to *verify* -- the
dashboard's own password. Encryption is for values you need to *use*.

The key lives in `BROKER_TOKEN_KEY`, outside the database, so a leaked database
dump yields ciphertext and nothing else. AES-GCM rather than AES-CBC because it
authenticates: a corrupted blob fails *here*, with an error that names the
problem, instead of at the broker with an opaque 403.

Expiry mirrors Kite's own rule -- tokens die at 06:00 IST the next morning by
regulatory requirement -- so the stored `expires_at` cannot disagree with
reality. A Mongo TTL index on that field removes the row when the token dies,
which means "no live row" and "no live token" are the same condition rather
than two that can drift apart.

Pure module: no repository, no settings, no clock. The caller supplies `now`.
"""

from __future__ import annotations

import base64
import os
from datetime import datetime, time, timedelta, timezone

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from algotrade.platform import clock

#: AES-256. A 16-byte key would still work and be materially weaker, so the
#: length is checked rather than assumed.
KEY_BYTES: int = 32

#: 96 bits, the size AES-GCM is specified for.
NONCE_BYTES: int = 12

#: Kite invalidates every access token at this hour, IST (design §5).
KITE_EXPIRY_HOUR: int = 6

#: Stored expiry is held this far BEFORE Kite's own, and the difference is not
#: about the TTL sweep -- every read checks expiry itself, so a late sweep is
#: already harmless.
#:
#: It buys two things the read-check cannot. Our clock and Kite's are not the
#: same clock: a token we believe has four seconds left may already be gone on
#: their side, and the symptom is a 403 rather than anything legible. And a
#: session that passes the check at the start of an action can die during it.
#:
#: With the margin, "live" carries a promise -- at least this much usable time
#: remains -- instead of merely "not provably dead yet".
SAFETY_MARGIN: timedelta = timedelta(minutes=2)


class TokenError(RuntimeError):
    """The token could not be encrypted, decoded or authenticated."""


def generate_key() -> bytes:
    """A fresh AES-256 key. For `BROKER_TOKEN_KEY`, and for tests."""
    return AESGCM.generate_key(bit_length=256)


def _checked(key: bytes) -> bytes:
    if len(key) != KEY_BYTES:
        raise TokenError(
            f"BROKER_TOKEN_KEY must be {KEY_BYTES} bytes, got {len(key)}"
        )
    return key


def encrypt(plaintext: str, *, key: bytes) -> str:
    """Encrypt to a base64 string of `nonce || ciphertext`.

    A fresh random nonce every call, so two encryptions of the same token do
    not produce the same blob -- otherwise the database would reveal that a
    session was reused.
    """
    nonce = os.urandom(NONCE_BYTES)
    sealed = AESGCM(_checked(key)).encrypt(nonce, plaintext.encode(), None)
    return base64.b64encode(nonce + sealed).decode()


def decrypt(blob: str, *, key: bytes) -> str:
    """Reverse `encrypt`. Raises `TokenError` on a wrong key or tampering."""
    try:
        raw = base64.b64decode(blob, validate=True)
    except Exception as exc:
        raise TokenError("token blob is not valid base64") from exc
    if len(raw) <= NONCE_BYTES:
        raise TokenError("token blob is too short to contain a nonce")
    nonce, sealed = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        return AESGCM(_checked(key)).decrypt(nonce, sealed, None).decode()
    except InvalidTag as exc:
        raise TokenError(
            "token failed authentication -- wrong key, or the stored value "
            "was modified"
        ) from exc


def kite_expiry(now: datetime) -> datetime:
    """The next 06:00 **IST** strictly after `now`, as an aware UTC instant.

    `now` must be timezone-aware, for the reason `codec.to_mongo_instant`
    gives: assuming a naive value means UTC is how a 5.5-hour error enters a
    stored record written from an IST machine. Here it would be worse than
    5.5 hours -- between 18:30 and 24:00 UTC the IST date is already tomorrow,
    so the wrong day's 06:00 would be computed and the session would be
    declared dead a full day early, or alive a full day late.

    At exactly 06:00 IST the current token is already gone, so the answer is
    tomorrow: the boundary belongs to the day that is starting.
    """
    if now.tzinfo is None:
        raise TokenError(
            f"naive datetime {now!r} has no timezone -- attach one explicitly "
            f"(platform.clock returns aware instants)"
        )
    ist = now.astimezone(clock.IST)
    boundary = datetime.combine(ist.date(), time(hour=KITE_EXPIRY_HOUR),
                                tzinfo=clock.IST)
    if ist >= boundary:
        boundary += timedelta(days=1)
    return boundary.astimezone(timezone.utc)


def expires_after(now: datetime) -> datetime:
    """What we store and trust: Kite's expiry, less `SAFETY_MARGIN`.

    Deliberately earlier than the truth. Being wrong in this direction costs a
    re-authorisation, which takes seconds; being wrong in the other direction
    costs a 403 in the middle of a rebalance.
    """
    return kite_expiry(now) - SAFETY_MARGIN


def is_usable(expires_at: datetime | None, now: datetime) -> bool:
    """Whether a stored session may still be used.

    One place decides this, because the repository, the view and any future
    job must not be able to disagree about whether a token is alive.
    """
    return expires_at is not None and expires_at > now
