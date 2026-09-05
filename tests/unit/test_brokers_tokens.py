"""Tests for session-token encryption (design §5).

The mistake this file exists to prevent: **hashing the access token.** Hashing
is one-way, and the plaintext is required for Kite's
`Authorization: token api_key:access_token` header -- so a hashed token stores
something that can never be used. Encryption is for things you need to *use*;
hashing is for things you only need to *verify*.

The other property worth a test is that the ciphertext is not deterministic. A
fixed nonce would make two encryptions of the same token identical, which leaks
that the token was reused across sessions.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from algotrade.brokers import tokens

KEY = tokens.generate_key()


def test_a_token_survives_a_round_trip():
    """The whole point: what comes back must be usable as a header value."""
    plain = "aBcD1234efGH5678"
    assert tokens.decrypt(tokens.encrypt(plain, key=KEY), key=KEY) == plain


def test_the_ciphertext_does_not_contain_the_token():
    plain = "aBcD1234efGH5678"
    assert plain not in tokens.encrypt(plain, key=KEY)


def test_encrypting_twice_gives_different_ciphertext():
    """A fixed nonce would leak that the same token was stored twice."""
    plain = "aBcD1234efGH5678"
    assert tokens.encrypt(plain, key=KEY) != tokens.encrypt(plain, key=KEY)


def test_the_wrong_key_cannot_decrypt():
    blob = tokens.encrypt("aBcD1234efGH5678", key=KEY)
    with pytest.raises(tokens.TokenError):
        tokens.decrypt(blob, key=tokens.generate_key())


def test_tampered_ciphertext_is_rejected_rather_than_returning_garbage():
    """AES-GCM authenticates. A silently corrupted token would fail later, at
    the broker, with an error that says nothing about what went wrong."""
    blob = tokens.encrypt("aBcD1234efGH5678", key=KEY)
    tampered = blob[:-4] + ("AAAA" if not blob.endswith("AAAA") else "BBBB")
    with pytest.raises(tokens.TokenError):
        tokens.decrypt(tampered, key=KEY)


def test_a_malformed_blob_is_rejected():
    with pytest.raises(tokens.TokenError):
        tokens.decrypt("not base64 at all!!", key=KEY)


def test_a_short_key_is_refused():
    """A 16-byte key would still 'work' and be materially weaker."""
    with pytest.raises(tokens.TokenError, match="32 bytes"):
        tokens.encrypt("x", key=b"too-short")


# --------------------------------------------------------------- expiry ---

def test_expiry_is_the_next_six_am_ist():
    """Kite's own regulatory expiry, so the stored record cannot disagree
    with reality."""
    got = tokens.expires_after(datetime(2026, 10, 1, 9, 20))
    assert got == datetime(2026, 10, 2, 6, 0)


def test_expiry_before_six_am_is_the_same_morning():
    """A token minted at 05:00 dies at 06:00, an hour later -- not 29."""
    got = tokens.expires_after(datetime(2026, 10, 1, 5, 0))
    assert got == datetime(2026, 10, 1, 6, 0)


def test_expiry_exactly_at_six_am_rolls_to_the_next_day():
    got = tokens.expires_after(datetime(2026, 10, 1, 6, 0))
    assert got == datetime(2026, 10, 2, 6, 0)
