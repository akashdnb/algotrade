"""Fund/ETF detection in the tradeable universe.

Found live on 2026-08-20: three precious-metal ETFs had reached the top 60 of
the momentum ranking, two of them inside the entry threshold of 30, because
`DEFAULT_EXCLUSIONS` is a hand-maintained denylist and nobody had added them.
Metal ETFs rank high on momentum exactly when gold and silver run, so the
failure is correlated with when it matters most.
"""

from __future__ import annotations

from algotrade.core import universe as uv




def _panel_with(symbols):
    """Minimal liquid panel: every name clears the turnover and coverage floors."""
    import polars as pl
    from datetime import date, timedelta
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(25)]
    rows = [{"date": d, "symbol": s, "close": 100.0, "turnover": 1e9}
            for s in symbols for d in days]
    return pl.DataFrame(rows)


# ------------------------------------------------------ fund detection ------
# Found live on 2026-08-20: three precious-metal ETFs had reached the top 60,
# two inside the entry threshold, because DEFAULT_EXCLUSIONS is a hand-kept
# denylist and nobody had added them.


def test_the_etf_suffix_rule_needs_no_maintenance():
    """A new *ETF or *BEES listing is caught without editing any list."""
    for symbol in ("SILVERIETF", "GOLDIETF", "NIFTYBEES", "CPSEETF", "NIFTYIETF"):
        assert uv.looks_like_fund(symbol), symbol


def test_real_companies_are_not_caught_by_the_suffix_rule():
    for symbol in ("SKYGOLD", "RELIANCE", "TCS", "GOLDIAM"):
        assert not uv.looks_like_fund(symbol), symbol


def test_membership_excludes_funds_structurally_not_just_by_denylist():
    panel = _panel_with(["RELIANCE", "BRANDNEWETF", "SKYGOLD"])
    members = uv.membership(panel, size=10)
    included = set(members["symbol"].to_list())
    assert "BRANDNEWETF" not in included, "a new ETF must be excluded on sight"
    assert "RELIANCE" in included or "SKYGOLD" in included


def test_suspected_funds_flags_for_review_without_excluding():
    """Silently dropping a real company is as wrong as silently buying a fund,
    so ambiguous names are surfaced rather than filtered."""
    flagged = uv.suspected_funds(["MOTILALGOLD", "TATASILVER", "RELIANCE"])
    assert flagged == ["MOTILALGOLD", "TATASILVER"]


def test_confirmed_real_companies_do_not_keep_flagging():
    """A detector that cries wolf is one that gets ignored."""
    assert uv.suspected_funds(["SKYGOLD", "NAVINFLUOR", "GOLDIAM"]) == []


def test_already_excluded_names_are_not_re_flagged():
    assert uv.suspected_funds(["NIFTYBEES", "GOLDBEES"]) == []
