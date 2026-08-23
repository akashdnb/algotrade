"""Tests for the dead-man switch and the Telegram client.

The heartbeat is the only monitor not hosted on the infrastructure it watches,
so its two non-obvious properties are tested explicitly: it derives /start and
/fail from ONE base URL (three separate URLs would be three separate checks),
and it never raises -- a monitoring failure must not fail a run that otherwise
succeeded.
"""

from __future__ import annotations

import pytest
import requests

from algotrade.platform import health, telegram


class FakeResponse:
    def __init__(self, ok=True, status_code=200, payload=None, text=""):
        self.ok = ok
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class Recorder:
    """Captures calls instead of making them."""

    def __init__(self, responses=None, raises=None):
        self.calls: list[tuple[str, bytes]] = []
        self.responses = list(responses or [])
        self.raises = raises

    def post(self, url, data=None, timeout=None, json=None):
        self.calls.append((url, data))
        if self.raises:
            raise self.raises
        return self.responses.pop(0) if self.responses else FakeResponse()

    @property
    def urls(self):
        return [u for u, _ in self.calls]


# ----------------------------------------------------------- heartbeat ------


def test_start_success_and_fail_derive_from_one_base_url(monkeypatch):
    """healthchecks.io models one check as one URL with suffixes. Three
    separate URLs would be three checks with independent timeouts."""
    rec = Recorder()
    monkeypatch.setattr(health.requests, "post", rec.post)

    hb = health.Heartbeat("https://hc-ping.com/uuid")
    hb.start()
    hb.success("ok")
    hb.fail("boom")

    assert rec.urls == [
        "https://hc-ping.com/uuid/start",
        "https://hc-ping.com/uuid",
        "https://hc-ping.com/uuid/fail",
    ]


def test_a_trailing_slash_does_not_double_up(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(health.requests, "post", rec.post)
    health.Heartbeat("https://hc-ping.com/uuid/").start()
    assert rec.urls == ["https://hc-ping.com/uuid/start"]


def test_explicit_overrides_win(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(health.requests, "post", rec.post)

    hb = health.Heartbeat(
        "https://base", start_url="https://a/start",
        success_url="https://b", fail_url="https://c/fail")
    hb.start(); hb.success(); hb.fail()
    assert rec.urls == ["https://a/start", "https://b", "https://c/fail"]


def test_the_summary_is_sent_as_the_body(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(health.requests, "post", rec.post)
    health.Heartbeat("https://hc-ping.com/uuid").success("sessions=3")
    assert rec.calls[0][1] == b"sessions=3"


def test_a_huge_body_is_truncated_rather_than_rejected(monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(health.requests, "post", rec.post)
    health.Heartbeat("https://hc-ping.com/uuid").success("x" * 50_000)
    assert len(rec.calls[0][1]) == 10_000


def test_a_network_failure_is_swallowed_not_raised(monkeypatch):
    """A monitoring failure must not fail a run that otherwise succeeded."""
    rec = Recorder(raises=requests.ConnectionError("no route"))
    monkeypatch.setattr(health.requests, "post", rec.post)
    assert health.Heartbeat("https://hc-ping.com/uuid").success() is False


def test_a_non_ok_response_reports_failure_without_raising(monkeypatch):
    rec = Recorder(responses=[FakeResponse(ok=False, status_code=500)] * 3)
    monkeypatch.setattr(health.requests, "post", rec.post)
    assert health.Heartbeat("https://hc-ping.com/uuid").success() is False


def test_an_unconfigured_heartbeat_is_a_no_op():
    hb = health.Heartbeat("")
    assert not hb.enabled
    assert hb.start() is False and hb.success() is False and hb.fail() is False


def test_the_null_heartbeat_needs_no_branching_at_call_sites():
    null = health.NullHeartbeat()
    assert not null.enabled
    assert null.start() is False and null.success() is False and null.fail() is False


def test_from_settings_returns_null_when_unconfigured():
    class S:
        healthchecks_configured = False
    assert isinstance(health.from_settings(S()), health.NullHeartbeat)


def test_from_settings_wires_the_overrides():
    class S:
        healthchecks_configured = True
        healthchecks_ping_url = "https://base"
        healthchecks_start_url = "https://a"
        healthchecks_success_url = ""
        healthchecks_fail_url = ""
    hb = health.from_settings(S())
    assert isinstance(hb, health.Heartbeat) and hb.enabled


# ------------------------------------------------------------ telegram ------


def test_markdownv2_reserved_characters_are_escaped():
    """An unescaped character is a 400, so the message silently never arrives."""
    assert telegram.escape("a-b.c!") == "a\\-b\\.c\\!"
    assert telegram.escape("(x)") == "\\(x\\)"


def test_plain_text_is_untouched():
    assert telegram.escape("RELIANCE 100") == "RELIANCE 100"


def test_a_short_message_is_not_split():
    assert telegram.split_message("hello") == ["hello"]


def test_splitting_happens_on_line_boundaries():
    """So a table or list is never cut mid-row."""
    text = "\n".join(f"row {i}" for i in range(100))
    chunks = telegram.split_message(text, limit=50)
    assert len(chunks) > 1
    assert all(len(c) <= 50 for c in chunks)
    assert "".join(c.replace("\n", "") for c in chunks) == text.replace("\n", "")


def test_a_single_over_long_line_is_hard_split_rather_than_dropped():
    chunks = telegram.split_message("x" * 120, limit=50)
    assert len(chunks) == 3
    assert "".join(chunks) == "x" * 120


def test_a_client_needs_both_a_token_and_a_chat():
    with pytest.raises(telegram.TelegramError):
        telegram.TelegramClient("", "chat")
    with pytest.raises(telegram.TelegramError):
        telegram.TelegramClient("token", "")


def test_send_returns_the_message_ids():
    class Session:
        def post(self, url, json=None, timeout=None):
            return FakeResponse(payload={"ok": True, "result": {"message_id": 7}})

    bot = telegram.TelegramClient("t", "c", session=Session())
    assert bot.send("hello") == [7]


def test_a_400_is_not_retried():
    """It is our bug -- bad markup or a bad chat id -- and will not fix itself;
    retrying only delays the real error."""
    calls = {"n": 0}

    class Session:
        def post(self, url, json=None, timeout=None):
            calls["n"] += 1
            return FakeResponse(ok=False, status_code=400,
                                payload={"ok": False, "description": "bad markup"})

    with pytest.raises(telegram.TelegramError, match="bad markup"):
        telegram.TelegramClient("t", "c", session=Session()).send("x")
    assert calls["n"] == 1


def test_a_long_message_is_sent_as_several():
    class Session:
        def __init__(self):
            self.n = 0

        def post(self, url, json=None, timeout=None):
            self.n += 1
            return FakeResponse(payload={"ok": True, "result": {"message_id": self.n}})

    bot = telegram.TelegramClient("t", "c", session=Session())
    ids = bot.send("\n".join("line" for _ in range(2000)))
    assert len(ids) > 1
