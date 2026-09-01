"""Fills and the tax export.

The financial-year boundary gets the most attention here: it is the one piece
of this page that feeds a tax return, and an off-by-one-day error moves a
trade into the wrong year silently.
"""

from __future__ import annotations

import csv
import io
from datetime import date

import pytest

from app.repositories.executions import financial_year, fy_bounds, fy_label

# ------------------------------------------------------- financial year ---


@pytest.mark.parametrize("day,expected", [
    (date(2026, 4, 1), 2026),      # first day of FY 2026-27
    (date(2026, 8, 28), 2026),
    (date(2027, 3, 31), 2026),     # last day of the SAME financial year
    (date(2027, 4, 1), 2027),      # next one begins
    (date(2026, 3, 31), 2025),     # the day before it started
    (date(2026, 1, 15), 2025),     # January belongs to the year before
])
def test_the_indian_financial_year_runs_april_to_march(day, expected):
    assert financial_year(day) == expected


def test_fy_bounds_are_half_open():
    """Ending on the next 1 April rather than 31 March, so no fill can fall
    between two years and vanish from both exports."""
    start, end = fy_bounds(2026)
    assert start == date(2026, 4, 1)
    assert end == date(2027, 4, 1)
    assert financial_year(date(2027, 3, 31)) == 2026    # inside
    assert financial_year(end) == 2027                  # excluded


def test_consecutive_years_do_not_overlap_or_gap():
    assert fy_bounds(2026)[1] == fy_bounds(2027)[0]


def test_fy_label_reads_the_way_a_tax_form_does():
    assert fy_label(2026) == "2026-27"
    assert fy_label(2019) == "2019-20"


# -------------------------------------------------------------- listing ---


def test_the_page_defaults_to_a_single_financial_year(client, auth):
    """The tax question is always about one year, never about everything."""
    body = client.get("/executions", auth=auth).text
    assert "GVT&amp;D" in body or "GVT&D" in body
    assert "OLDCO" not in body           # it is in FY 2025


def test_an_earlier_year_can_be_selected(client, auth):
    body = client.get("/executions?fy=2025", auth=auth).text
    assert "OLDCO" in body
    assert "HFCL" not in body


def test_filtering_by_side(client, auth):
    assert "HFCL" in client.get("/executions?fy=2026&side=buy", auth=auth).text
    assert "HFCL" not in client.get("/executions?fy=2026&side=sell", auth=auth).text


def test_filtering_by_symbol_is_case_insensitive(client, auth):
    assert "HFCL" in client.get("/executions?fy=2026&symbol=hfcl", auth=auth).text


def test_only_years_with_fills_are_offered(client, auth):
    body = client.get("/executions", auth=auth).text
    assert "2026-27" in body and "2025-26" in body
    assert "2019-20" not in body


# ------------------------------------------------------------------ csv ---


def _rows(body: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(body)))


def test_the_csv_exports_the_whole_year_not_a_page(client, auth):
    """A tax export that silently stopped at the first page would be worse
    than none: it would look complete."""
    rows = _rows(client.get("/executions.csv?fy=2026", auth=auth).text)
    assert {r["symbol"] for r in rows} == {"GVT&D", "HFCL"}


def test_the_csv_is_named_for_its_financial_year(client, auth):
    r = client.get("/executions.csv?fy=2026", auth=auth)
    assert 'filename="executions-paper_momentum_v1_001-FY2026-27.csv"' in \
        r.headers["content-disposition"]


def test_csv_numbers_are_machine_readable(client, auth):
    """No rupee signs, no thousands separators, paise preserved -- a
    spreadsheet must not have to parse currency, and a float would lose
    precision that a tax return depends on."""
    rows = _rows(client.get("/executions.csv?fy=2026", auth=auth).text)
    gvt = next(r for r in rows if r["symbol"] == "GVT&D")
    assert gvt["price"] == "4050.00"
    assert gvt["value"] == "8100.00"
    assert gvt["charges"] == "9.82"
    assert "₹" not in gvt["price"] and "," not in gvt["value"]


def test_the_csv_respects_the_filters(client, auth):
    rows = _rows(client.get("/executions.csv?fy=2026&symbol=HFCL", auth=auth).text)
    assert [r["symbol"] for r in rows] == ["HFCL"]


def test_the_csv_carries_the_ids_needed_to_trace_a_row_back(client, auth):
    rows = _rows(client.get("/executions.csv?fy=2026", auth=auth).text)
    assert all(r["recommendation_set"] and r["execution_id"] for r in rows)
