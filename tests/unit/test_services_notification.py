"""Tests for message rendering and the send-once guard.

The templates are what you read every evening for years, so their content is a
design decision, not cosmetics: drawdown is as prominent as return, the daily
message carries no trade list, and a failure states its blast radius.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from algotrade.domain import money
from algotrade.platform.telegram import TelegramError
from algotrade.services.notification import (
    DailyFacts,
    NotificationService,
    render_daily,
    render_ingest_failure,
    render_stale_data,
)

D = date
R = money.rupees
NOW = datetime(2026, 9, 30, 14, 30, tzinfo=timezone.utc)


def plain(text: str) -> str:
    """Strip MarkdownV2 escapes so assertions read as the human sees them.

    Telegram requires a literal hyphen to be sent backslash-escaped;
    asserting on the wire form would make these tests about escaping
    rather than about content.
    """
    out, i = [], 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text):
            out.append(text[i + 1])
            i += 2
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def _facts(**over) -> DailyFacts:
    kwargs = dict(
        portfolio_id="paper_momentum_v1_001", label="Paper Momentum 30",
        as_of=D(2026, 9, 30), nav=R("284310.22"), cash=R("14320.55"),
        day_change=R("3266.10"), day_change_pct=0.0116, holdings_count=29,
        month_return=0.0421, month_benchmark=0.0208,
        ytd_return=0.1783, ytd_benchmark=0.1104,
        drawdown=-0.0297, peak_date=D(2026, 9, 12),
        best=[("PERSISTENT", 0.048)], worst=[("ADANIENT", -0.032)],
    )
    kwargs.update(over)
    return DailyFacts(**kwargs)


# ------------------------------------------------------------ formatting ----


def test_rupees_use_indian_digit_grouping():
    """2,84,310 -- not 284,310. Read daily by an Indian investor."""
    from algotrade.services.notification import _rupees

    assert _rupees(R("284310.22")) == "₹2,84,310"
    assert _rupees(R("999.00")) == "₹999"
    assert _rupees(R("100000.00")) == "₹1,00,000"
    assert _rupees(R("-4220.00")) == "-₹4,220"
    assert _rupees(None) == "-"


def test_percentages_are_signed_so_direction_is_unambiguous():
    from algotrade.services.notification import _pct

    assert _pct(0.0421) == "+4.21%"
    assert _pct(-0.0297) == "-2.97%"
    assert _pct(None) == "-"


# ----------------------------------------------------------- daily message --


def test_the_daily_message_states_portfolio_and_date():
    """Read months later with no surrounding context."""
    text = plain(render_daily(_facts()))
    assert "paper_momentum_v1_001" in text and "30 Sep 2026" in text


def test_the_daily_message_carries_no_trade_list():
    """The strategy rebalances monthly and the buffer exists so drift is NOT
    traded. A daily buy/sell list is a machine for tempting you to overtrade
    against a cost case that assumed ~21% annual turnover."""
    text = render_daily(_facts()).upper()
    assert "BUY" not in text
    assert "SELL" not in text


def test_drawdown_is_always_shown():
    """§11p priced quitting during a streak at 27-40% of terminal wealth. A
    message showing only gains trains exactly the wrong reflex."""
    assert "Drawdown" in render_daily(_facts())
    assert "-2.97%" in render_daily(_facts())


def test_unexecuted_items_are_surfaced():
    text = plain(render_daily(_facts(unexecuted_items=2, unexecuted_age_days=3)))
    assert "2 item" in text and "3 day" in text


def test_no_warning_when_nothing_is_outstanding():
    assert "unexecuted" not in render_daily(_facts())


def test_a_long_streak_is_shown_against_its_historical_worst():
    """So 84 days reads as normal rather than as evidence of breakage.
    §11o P5: the historical worst is 1,203 days and dilution does not shorten it."""
    text = plain(render_daily(_facts(current_streak_days=84)))
    assert "84 days" in text and "1,203" in text


def test_a_short_streak_is_not_mentioned():
    assert "Behind NIFTY" not in render_daily(_facts(current_streak_days=5))


def test_the_next_rebalance_is_context_not_an_instruction():
    text = plain(render_daily(_facts(next_rebalance=D(2026, 10, 31),
                                     sessions_to_rebalance=22)))
    assert "Next rebalance" in text and "22 sessions" in text


def test_markdown_reserved_characters_are_escaped():
    """An unescaped character is a 400 from Telegram, not a mangled message --
    so the whole notification silently fails to arrive."""
    text = render_daily(_facts())
    assert "\\_" in text or "\\(" in text or "\\-" in text


def test_a_minimal_facts_object_still_renders():
    text = render_daily(DailyFacts(
        portfolio_id="paper_momentum_v1_001", label="", as_of=D(2026, 9, 30),
        nav=R("200000.00"), cash=R("200000.00")))
    assert "2,00,000" in text


# --------------------------------------------------------------- failures ---


def test_a_failure_states_its_blast_radius_and_whether_it_self_heals():
    """An alert without those is one you learn to ignore."""
    text = render_ingest_failure(
        business_date=D(2026, 9, 30), stage="daily",
        detail="NseFetchError: giving up after 3 attempts",
        run_url="https://github.com/x/y/actions/runs/1")
    assert "Impact" in plain(text)
    assert "Self-heals" in plain(text)
    assert "NseFetchError" in plain(text)


def test_a_non_self_healing_failure_says_so():
    text = render_ingest_failure(business_date=D(2026, 9, 30), stage="rebalance",
                                 detail="x", self_heals=False)
    assert "does not self-heal" in plain(text).lower()


def test_the_stale_data_message_names_the_missing_sessions_and_the_fix():
    text = render_stale_data(as_of=D(2026, 10, 2), last_session=D(2026, 9, 29),
                             missing=[D(2026, 9, 30), D(2026, 10, 1)],
                             days_behind=2)
    assert "2026-09-30" in plain(text)
    assert "BLOCKED" in plain(text)
    assert "recover.yml" in plain(text)


# --------------------------------------------------------- send-once ------


class FakeNotifications:
    def __init__(self, already: bool = False) -> None:
        self.already = already
        self.sent: list[str] = []
        self.failed: list[tuple[str, str]] = []

    def claim(self, **kwargs):
        return None if self.already else "notif:1"

    def mark_sent(self, _id, *, now, telegram_message_id=None):
        self.sent.append(_id)
        return True

    def mark_failed(self, _id, *, error):
        self.failed.append((_id, error))
        return True


class FakeTelegram:
    def __init__(self, fail: Exception | None = None) -> None:
        self.fail = fail
        self.messages: list[str] = []

    def send(self, text, *, silent=False, markdown=True):
        if self.fail:
            raise self.fail
        self.messages.append(text)
        return [42]


def test_a_message_is_sent_once_and_recorded():
    repo, bot = FakeNotifications(), FakeTelegram()
    service = NotificationService(repository=repo, client=bot)
    assert service.send(kind="daily_summary", text="hi", now=NOW) == "notif:1"
    assert bot.messages == ["hi"]
    assert repo.sent == ["notif:1"]


def test_an_already_claimed_message_is_not_re_sent():
    """A re-run of the daily job must not post a second summary."""
    repo, bot = FakeNotifications(already=True), FakeTelegram()
    service = NotificationService(repository=repo, client=bot)
    assert service.send(kind="daily_summary", text="hi", now=NOW) is None
    assert bot.messages == []


def test_a_telegram_failure_is_recorded_and_never_raised():
    """A broken alert channel must not fail a run that otherwise succeeded --
    but it must be visible to the watchdog, because a silent delivery failure
    is invisible by definition."""
    repo = FakeNotifications()
    service = NotificationService(
        repository=repo, client=FakeTelegram(fail=TelegramError("429")))
    assert service.send(kind="daily_summary", text="hi", now=NOW) == "notif:1"
    assert repo.failed and "429" in repo.failed[0][1]
    assert repo.sent == []


def test_an_unexpected_error_is_also_contained():
    repo = FakeNotifications()
    service = NotificationService(
        repository=repo, client=FakeTelegram(fail=RuntimeError("boom")))
    service.send(kind="daily_summary", text="hi", now=NOW)
    assert repo.failed and "RuntimeError" in repo.failed[0][1]


def test_no_client_configured_records_a_failure_rather_than_crashing():
    repo = FakeNotifications()
    NotificationService(repository=repo, client=None).send(
        kind="daily_summary", text="hi", now=NOW)
    assert repo.failed and "no client" in repo.failed[0][1]
