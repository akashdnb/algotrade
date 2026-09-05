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
from datetime import datetime, time, timedelta

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

#: AES-256. A 16-byte key would still work and be materially weaker, so the
#: length is checked rather than assumed.
KEY_BYTES: int = 32

#: 96 bits, the size AES-GCM is specified for.
NONCE_BYTES: int = 12

#: Kite invalidates every access token at this hour, IST (design §5).
EXPIRY_HOUR: int = 6


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


def expires_after(now: datetime) -> datetime:
    """The next 06:00 that follows `now`, which is when Kite drops the token.

    `now` is naive IST, matching how the rest of the system carries business
    time. At exactly 06:00 the current token is already gone, so the answer is
    tomorrow -- the boundary belongs to the day that is starting.
    """
    today = datetime.combine(now.date(), time(hour=EXPIRY_HOUR))
    return today if now < today else today + timedelta(days=1)
