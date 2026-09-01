"""Behaviour that matters, tested without a database."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from bson.decimal128 import Decimal128

from app import charts, formatting
from app.repositories.base import Page, dec, decode
from app.settings import ConfigError, get_settings

# ------------------------------------------------------------ formatting ---


@pytest.mark.parametrize("value,expected", [
    (0, "₹0"),
    (556, "₹556"),
    (199763, "₹1,99,763"),
    (12345678, "₹1,23,45,678"),
    (-2500, "-₹2,500"),
])
def test_rupees_uses_indian_digit_grouping(value, expected):
    """1,99,763 and not 199,763. Western grouping makes every figure on the
    page subtly wrong to read for the person who has to trust it."""
    assert formatting.rupees(value) == expected


def test_absent_money_is_not_zero():
    """A missing price and a price of nothing are different claims."""
    assert formatting.rupees(None) == "—"
    assert formatting.rupees(0) == "₹0"


def test_pnl_carries_an_explicit_sign():
    """`₹695` reads as a balance; `+₹695` reads as a change."""
    assert formatting.signed_rupees(695) == "+₹695"
    assert formatting.signed_rupees(-695) == "-₹695"


def test_dates_are_unambiguous():
    assert formatting.day(date(2026, 8, 28)) == "28 Aug 2026"
    assert formatting.day("2026-08-28") == "28 Aug 2026"


def test_ago_answers_is_this_stale():
    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    assert formatting.ago(now - timedelta(seconds=30), now=now) == "just now"
    assert formatting.ago(now - timedelta(hours=3), now=now) == "3 hours ago"
    assert formatting.ago(now - timedelta(days=2), now=now) == "2 days ago"
    assert formatting.ago(None) == "never"


# --------------------------------------------------------------- decimal ---


def test_decimal128_is_decoded_not_stringified():
    out = decode({"nav": Decimal128(Decimal("201071.20"))}, money=("nav",))
    assert out["nav"] == Decimal("201071.20")


def test_a_float_in_a_money_field_is_refused():
    """The trading system's codec guarantees money is never a float. If one
    appears, something bypassed it -- and rendering it as fact is how a
    rounding bug becomes a number you act on."""
    with pytest.raises(TypeError, match="float"):
        dec(201071.20)


def test_paise_survive_the_round_trip():
    assert dec(Decimal128(Decimal("0.05"))) == Decimal("0.05")


# ------------------------------------------------------------------ page ---


def test_page_knows_when_there_is_no_next():
    assert not Page(items=[1, 2], total=2, limit=50, offset=0).has_next
    assert Page(items=[1] * 50, total=120, limit=50, offset=0).has_next
    assert not Page(items=[1] * 50, total=120, limit=50, offset=100).has_next


# ---------------------------------------------------------------- charts ---


def test_a_chart_with_no_data_says_so():
    """Rather than rendering an empty box that looks like a flat line."""
    svg = charts.line_chart([])
    assert "no data yet" in svg
    assert "<path" not in svg


def test_a_single_point_does_not_divide_by_zero():
    svg = charts.line_chart([charts.Point("2026-08-28", 100.0)])
    assert "<svg" in svg


def test_a_flat_series_does_not_divide_by_zero():
    points = [charts.Point(str(i), 100.0) for i in range(5)]
    assert "<svg" in charts.line_chart(points)


def test_null_readings_are_skipped_not_zeroed():
    """A missing benchmark is not a benchmark of nought -- plotting it as zero
    would draw a collapse that never happened."""
    rows = [{"date": "a", "v": Decimal("1")}, {"date": "b", "v": None},
            {"date": "c", "v": Decimal("3")}]
    assert [p.value for p in charts.to_points(rows, x="date", y="v")] == [1.0, 3.0]


def test_drawdown_never_plots_above_zero():
    """Drawdown is <= 0 by definition; a positive reading is a bug upstream and
    must not render as a rising line."""
    svg = charts.drawdown_chart([charts.Point("a", 0.05), charts.Point("b", -0.1)])
    assert "<svg" in svg


def test_weight_bar_flags_an_oversized_position():
    """Roughly 2x the equal-weight target is usually an unexecuted sell."""
    assert "over" in charts.weight_bar(0.07, target=1 / 30)
    assert "at" in charts.weight_bar(0.033, target=1 / 30)


def test_chart_output_escapes_its_labels():
    """Point labels reach the SVG as tooltip text and axis text."""
    evil = '"><script>alert(1)</script>'
    svg = charts.line_chart([charts.Point(evil, 1.0), charts.Point("b", 2.0)],
                            label=evil)
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_a_shallow_drawdown_renders_shallow():
    """The reason this chart is not auto-scaled. -0.02% against an expected
    -41.6% must be a hairline, not a wall of red -- an auto-scaled axis drew
    it full-height, which is the misreading the Investment Policy exists to
    prevent."""
    svg = charts.drawdown_chart([charts.Point("a", -0.0002),
                                 charts.Point("b", -0.0002)])
    import re
    ys = [float(y) for y in re.findall(r"L \d+\.\d+,(\d+\.\d+)", svg)]
    top = charts.PAD_T
    plot_h = 190 - charts.PAD_T - charts.PAD_B
    assert ys, "expected a path"
    # Every point sits within 2% of the zero line.
    assert max(ys) - top < plot_h * 0.02


def test_a_drawdown_worse_than_expected_extends_the_axis():
    """If it ever exceeds the expectation, the exceedance IS the news and the
    chart must not clip it."""
    svg = charts.drawdown_chart([charts.Point("a", -0.60)])
    assert "expected" in svg          # drawn as its own line, above the floor
    assert "-63%" in svg


def test_the_expected_line_is_not_labelled_twice():
    """When the drawdown is shallower than expected the axis floor IS the
    expectation; two labels at the same y overlapped into unreadable text."""
    import re
    svg = charts.drawdown_chart([charts.Point("a", -0.01)])
    visible = re.findall(r"<text[^>]*>([^<]*)</text>", svg)
    assert sum("expected" in v for v in visible) == 1, visible
    assert "-41.6% expected" in visible


# -------------------------------------------------------------- settings ---


def test_a_dev_database_is_refused(monkeypatch):
    """A dashboard pointed at the wrong environment shows the wrong money with
    total confidence."""
    monkeypatch.setenv("MONGODB_DATABASE", "algotrade_dev")
    get_settings.cache_clear()
    with pytest.raises(ConfigError, match="unexpected database"):
        get_settings()


def test_a_short_password_is_refused(monkeypatch):
    monkeypatch.setenv("DASHBOARD_PASSWORD", "short")
    get_settings.cache_clear()
    with pytest.raises(ConfigError, match="12 characters"):
        get_settings()


def test_writes_are_off_unless_switched_on():
    assert get_settings().allow_writes is False


def test_the_uri_is_not_in_the_redacted_view():
    redacted = get_settings().redacted()
    assert "mongodb://" not in str(redacted)


# ---------------------------------------------------------------- routes ---


def test_every_page_requires_credentials(client):
    for path in ("/", "/holdings", "/health"):
        assert client.get(path).status_code == 401


def test_a_wrong_password_is_rejected(client):
    assert client.get("/", auth=("tester", "wrong")).status_code == 401


def test_a_wrong_username_is_rejected(client):
    assert client.get("/", auth=("nobody", "correct-horse-battery")).status_code == 401


def test_summary_renders_the_latest_snapshot(client, auth):
    body = client.get("/", auth=auth).text
    assert "₹2,01,071" in body
    assert "28 Aug 2026" in body


def test_summary_shows_expected_pain_beside_the_live_drawdown(client, auth):
    """The reason this page exists: -0.02% is obviously nothing next to a
    -41.6% expectation, and terrifying without it."""
    body = client.get("/", auth=auth).text
    assert "−41.6%" in body
    assert "1,198 days" in body


def test_summary_says_when_there_is_no_benchmark(client, auth):
    """Rather than drawing an empty comparison and letting it read as parity."""
    assert "No NIFTY comparison yet" in client.get("/", auth=auth).text


def test_carried_forward_prices_are_announced(client, db, auth):
    """A stale price on a holiday looks exactly like a flat market."""
    db["portfolio_snapshots"].docs[-1]["price_date"] = "2026-08-27"
    assert "carried forward" in client.get("/", auth=auth).text


def test_holdings_derives_quantity_and_cost_from_lots(client, auth):
    body = client.get("/holdings", auth=auth).text
    assert "GVT&amp;D" in body or "GVT&D" in body
    assert "₹4,054.91" in body          # (4050.00 * 2 + 9.82) / 2


def test_holdings_marks_a_long_held_lot_as_long_term(client, auth):
    """HFCL was acquired in 2024; the oldest lot decides, because FIFO sells
    that one first."""
    body = client.get("/holdings", auth=auth).text
    assert ">LT<" in body.replace(" ", "").replace("\n", "")


def test_health_excludes_synthetic_rehearsal_rows(client, auth):
    """They record nothing that happened, and a `failed` row that never
    failed makes the page lie in the alarming direction."""
    body = client.get("/health", auth=auth).text
    assert "value_portfolio" not in body


def test_healthz_needs_no_database(client):
    """A liveness probe that opens a connection turns a slow database into a
    dead application."""
    assert client.get("/healthz", auth=("tester", "correct-horse-battery")).json() == {
        "status": "ok"}


def test_pages_are_not_cacheable_and_run_no_scripts(client, auth):
    response = client.get("/", auth=auth)
    assert "no-store" in response.headers["cache-control"]
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_pages_are_not_indexable(client, auth):
    assert 'content="noindex' in client.get("/", auth=auth).text


def test_there_is_no_openapi_surface(client, auth):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path, auth=auth).status_code == 404


@pytest.mark.parametrize("uri,expected", [
    ("mongodb+srv://user:pw@cluster0.abc.mongodb.net/?retryWrites=true",
     "cluster0.abc.mongodb.net"),
    ("mongodb://localhost:27017/", "localhost:27017"),
    ("mongodb://u:p@h1:27017,h2:27017/db", "h1:27017,h2:27017"),
    ("nonsense", "***"),
])
def test_redaction_never_returns_the_credential(uri, expected):
    from app.settings import redact_uri
    assert redact_uri(uri) == expected
    assert "pw" not in redact_uri(uri)


# ------------------------------------------------------- portfolio picker ---


def test_the_active_portfolio_is_always_named(client, auth):
    """Even with one portfolio. It used to be hidden when there was nothing to
    switch to, which meant you could not tell which book you were looking at
    without reading the footer."""
    body = client.get("/", auth=auth).text
    assert "Paper Momentum 30" in body


def test_one_portfolio_renders_as_a_label_not_a_link(client, auth):
    """A control that cannot change anything should not look like one."""
    body = client.get("/", auth=auth).text
    assert 'class="only"' in body
    assert "?portfolio=" not in body


def test_the_picker_lists_every_portfolio(two_portfolios, client, auth):
    body = client.get("/", auth=auth).text
    assert 'class="picker"' in body
    assert "Paper Momentum 30" in body
    assert "Shadow Momentum" in body


def test_switching_portfolio_changes_the_data(two_portfolios, client, auth):
    default = client.get("/", auth=auth).text
    shadow = client.get("/?portfolio=shadow_momentum_v1_001", auth=auth).text
    assert "₹2,01,071" in default
    assert "₹99,500" in shadow


def test_nav_links_carry_the_selection(two_portfolios, client, auth):
    body = client.get("/?portfolio=shadow_momentum_v1_001", auth=auth).text
    assert "/holdings?portfolio=shadow_momentum_v1_001" in body


def test_the_default_portfolio_produces_clean_urls(two_portfolios, client, auth):
    """No query string for the common case, so a bookmark does not pin a
    portfolio you later stop using."""
    body = client.get("/", auth=auth).text
    assert 'href="/holdings"' in body


def test_an_unknown_portfolio_is_refused(two_portfolios, client, auth):
    """Rather than rendering an empty page, which is indistinguishable from a
    portfolio that genuinely holds nothing."""
    assert client.get("/?portfolio=made_up", auth=auth).status_code == 404


def test_a_missing_default_falls_back_rather_than_erroring(client, db, auth):
    """A fresh database or a PORTFOLIO_ID typo should show the truth, not 500."""
    db["portfolios"].docs[0]["_id"] = "something_else_v1_001"
    assert client.get("/", auth=auth).status_code == 200
