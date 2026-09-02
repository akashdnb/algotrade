"""Tests for forking a portfolio into a shadow (design §9).

The two properties that matter: the rewrite reaches EVERY id (a missed one
would silently link the shadow back to the source's trade record), and the
honesty gate fires the moment the source stops being a mechanical replay.
"""

from __future__ import annotations

from decimal import Decimal

from algotrade.services import fork

SOURCE = "paper_momentum_v1_001"
TARGET = "shadow_momentum_v1_001"


def _rewrite(doc):
    return fork.rewrite(doc, source=SOURCE, target=TARGET)


# ------------------------------------------------------------- rewrite ---

def test_the_primary_key_and_portfolio_are_rewritten():
    out = _rewrite({"_id": f"{SOURCE}:2026-08", "portfolio_id": SOURCE,
                    "period": "2026-08"})
    assert out["_id"] == f"{TARGET}:2026-08"
    assert out["portfolio_id"] == TARGET
    assert out["period"] == "2026-08"


def test_every_occurrence_is_replaced_not_just_the_leading_one():
    """A cash entry's id embeds the portfolio twice.

    `portfolio:kind:<execution id>`, and the execution id starts with the
    portfolio as well. Rewriting only the prefix would leave the shadow's
    ledger keyed partly by the source.
    """
    execution_id = f"{SOURCE}:2026-08:r1:3:1"
    out = _rewrite({
        "_id": f"{SOURCE}:buy:{execution_id}",
        "portfolio_id": SOURCE,
        "execution_id": execution_id,
    })
    assert SOURCE not in out["_id"]
    assert out["_id"] == f"{TARGET}:buy:{TARGET}:2026-08:r1:3:1"
    assert out["execution_id"] == f"{TARGET}:2026-08:r1:3:1"


def test_cross_references_between_collections_are_rewritten():
    out = _rewrite({
        "_id": f"{SOURCE}:2026-08:r1",
        "portfolio_id": SOURCE,
        "rebalance_run_id": f"{SOURCE}:2026-08",
        "recommendation_set_id": f"{SOURCE}:2026-08:r1",
        "set_id": f"{SOURCE}:2026-08:r1",
    })
    assert all(v == v.replace(SOURCE, TARGET) for v in out.values())
    assert not any(SOURCE in v for v in out.values() if isinstance(v, str))


def test_ids_nested_in_a_list_of_items_are_rewritten():
    out = _rewrite({
        "_id": f"{SOURCE}:2026-08:r1",
        "portfolio_id": SOURCE,
        "items": [{"seq": 1, "symbol": "TCS", "set_id": f"{SOURCE}:2026-08:r1"},
                  {"seq": 2, "symbol": "INFY", "set_id": f"{SOURCE}:2026-08:r1"}],
    })
    assert [i["set_id"] for i in out["items"]] == [f"{TARGET}:2026-08:r1"] * 2
    assert [i["symbol"] for i in out["items"]] == ["TCS", "INFY"]


def test_free_text_is_left_alone():
    """A note is a record of what someone wrote, not an id to be re-keyed."""
    note = f"entered by hand against {SOURCE} after the T+1 settlement"
    out = _rewrite({"_id": SOURCE, "portfolio_id": SOURCE, "note": note})
    assert out["note"] == note


def test_non_string_values_survive_untouched():
    out = _rewrite({"_id": SOURCE, "portfolio_id": SOURCE,
                    "quantity": 300, "price": Decimal("2402.75"),
                    "broker_order_id": ""})
    assert out["quantity"] == 300
    assert out["price"] == Decimal("2402.75")


# --------------------------------------------------- the honesty gate ---

def _fill(**over):
    base = {"_id": f"{SOURCE}:2026-08:r1:1:1", "symbol": "TCS",
            "price": Decimal("2400.00"), "reference_price": Decimal("2400.00"),
            "broker_order_id": ""}
    return {**base, **over}


def test_a_book_filled_only_at_reference_prices_can_be_forked():
    assert fork.discretion_evidence([_fill(), _fill(symbol="INFY")]) == []


def test_a_broker_order_id_is_divergence():
    """Only a real fill has one, and a real fill involved a human."""
    evidence = fork.discretion_evidence([_fill(broker_order_id="251001000123456")])
    assert len(evidence) == 1
    assert "251001000123456" in evidence[0]


def test_a_price_away_from_the_reference_is_divergence():
    evidence = fork.discretion_evidence([_fill(price=Decimal("2402.75"))])
    assert len(evidence) == 1
    assert "2402.75" in evidence[0]


def test_only_the_diverged_fills_are_reported():
    evidence = fork.discretion_evidence([
        _fill(), _fill(symbol="INFY", price=Decimal("1500.10")), _fill(),
    ])
    assert len(evidence) == 1
    assert "INFY" in evidence[0]


def test_a_missing_reference_price_is_not_treated_as_divergence():
    """Absent evidence is not evidence. An older fill without a reference
    price recorded cannot be judged, and guessing would refuse a fork that is
    fine."""
    assert fork.discretion_evidence([_fill(reference_price=None)]) == []


def test_holdings_are_not_copied():
    """They are rebuilt from the copied executions instead, which is what
    proves the copy reproduces the book rather than merely resembling it."""
    assert "holdings" not in fork.FORKED_COLLECTIONS
    assert "executions" in fork.FORKED_COLLECTIONS
