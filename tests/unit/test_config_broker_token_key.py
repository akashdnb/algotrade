"""BROKER_TOKEN_KEY is validated for shape at config time (design §5).

A wrong-sized key does not fail when it is set. It fails inside
`tokens.encrypt`, which runs on rebalance morning, after authorising with the
broker, with a session already open and thirty orders waiting. Catching it in
`check_config` moves that failure to a moment when nothing is at stake.
"""

from __future__ import annotations

import base64

from algotrade.brokers import tokens
from algotrade.config import validation
from algotrade.config.settings import Settings

GOOD_KEY = base64.b64encode(tokens.generate_key()).decode()


def _settings(**kwargs) -> Settings:
    args = dict(environment="paper", log_level="INFO",
                mongodb_uri="mongodb://localhost", mongodb_database="t")
    args.update(kwargs)
    return Settings(**args)


def _broker_findings(**kwargs):
    return list(validation._check_broker(_settings(**kwargs)))


def _keys(findings):
    return {f.key for f in findings}


def test_no_broker_configuration_at_all_is_merely_informational():
    findings = _broker_findings()
    assert [f.severity for f in findings] == [validation.Severity.INFO]


def test_a_well_formed_key_produces_no_key_finding():
    findings = _broker_findings(broker_token_key=GOOD_KEY)
    assert "BROKER_TOKEN_KEY" not in _keys(findings)


def test_a_key_of_the_wrong_length_is_an_error():
    """A 16-byte key would encrypt happily and be materially weaker."""
    short = base64.b64encode(b"x" * 16).decode()
    findings = _broker_findings(broker_token_key=short)
    bad = [f for f in findings if f.key == "BROKER_TOKEN_KEY"]
    assert bad and bad[0].severity is validation.Severity.ERROR
    assert "16 bytes" in bad[0].message


def test_a_key_that_is_not_base64_is_an_error():
    findings = _broker_findings(broker_token_key="not base64 at all!!")
    bad = [f for f in findings if f.key == "BROKER_TOKEN_KEY"]
    assert bad and bad[0].severity is validation.Severity.ERROR


def test_the_error_says_how_to_generate_a_correct_key():
    """The remedy belongs with the complaint; otherwise it is a scavenger
    hunt at exactly the wrong moment."""
    short = base64.b64encode(b"x" * 16).decode()
    bad = [f for f in _broker_findings(broker_token_key=short)
           if f.key == "BROKER_TOKEN_KEY"][0]
    assert "generate_key" in (bad.hint or "")


def test_broker_credentials_without_a_token_key_warn():
    """The session could not be stored, so the read-only section would ask for
    authorisation on every page load."""
    findings = _broker_findings(broker_api_key="k", broker_api_secret="s",
                                broker_name="zerodha")
    warned = [f for f in findings if f.key == "BROKER_TOKEN_KEY"]
    assert warned and warned[0].severity is validation.Severity.WARNING


def test_a_key_alone_is_enough_to_leave_the_not_configured_branch():
    """Otherwise a key set before the credentials would be silently unchecked."""
    findings = _broker_findings(broker_token_key="not base64 at all!!")
    assert "BROKER_TOKEN_KEY" in _keys(findings)


def test_a_generated_key_round_trips_through_validation_and_use():
    """The generator the remedy recommends must produce a key that passes."""
    key = base64.b64encode(tokens.generate_key()).decode()
    assert "BROKER_TOKEN_KEY" not in _keys(_broker_findings(broker_token_key=key))
    raw = base64.b64decode(key)
    assert tokens.decrypt(tokens.encrypt("abc", key=raw), key=raw) == "abc"
