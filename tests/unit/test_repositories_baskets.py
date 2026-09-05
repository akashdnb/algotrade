"""Tests for the accepted basket (design §6, option A).

The collection exists because `/review` recomputes the basket on every page
load while §6 fixes quantities for the whole rebalance. Without this row, each
per-trade click would act on a slightly different basket -- destroying the one
property that makes a per-trade button worth having: the number beside trade
28 is the number reviewed at the start.

So the properties under test are about *not changing*: insert rather than
upsert, prices stored as approved, and `seq` read back rather than recomputed.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from algotrade.repositories.base import AlreadyExists
from algotrade.repositories.baskets import BasketRepository

SET_ID = "paper_momentum_v1_001:2026-10:r1"
SESSION = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 9, 20, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self):
        self.docs = {}

    def insert_one(self, doc):
        if doc["_id"] in self.docs:
            from pymongo.errors import DuplicateKeyError
            raise DuplicateKeyError("dup")
        self.docs[doc["_id"]] = doc
        return type("R", (), {"inserted_id": doc["_id"]})()

    def find_one(self, query, projection=None):
        return self.docs.get(query.get("_id"))

    def find(self, query=None, projection=None):
        rows = [d for d in self.docs.values()
                if all(d.get(k) == v for k, v in (query or {}).items())]
        return _Cursor(rows)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, spec):
        for key, direction in reversed(spec):
            self.rows.sort(key=lambda r: r.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n] if n else self.rows
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeDb:
    def __init__(self):
        self._c = {}

    def __getitem__(self, name):
        return self._c.setdefault(name, FakeCollection())


def _repo() -> BasketRepository:
    return BasketRepository(FakeDb())


def _lines():
    return [
        {"seq": 1, "action": "sell", "symbol": "OLD", "quantity": 5,
         "price": Decimal("100.00"), "estimated_charges": Decimal("15.34"),
         "order_type": "MARKET"},
        {"seq": 2, "action": "buy", "symbol": "NEW", "quantity": 3,
         "price": Decimal("224.17"), "estimated_charges": Decimal("2.00")},
    ]


def _accept(repo, **kwargs):
    args = dict(portfolio_id="paper_momentum_v1_001", set_id=SET_ID,
                session=SESSION, lines=_lines(), now=NOW)
    args.update(kwargs)
    return repo.accept(**args)


# ------------------------------------------------------------- accepting ---

def test_an_accepted_basket_is_readable_back():
    repo = _repo()
    _accept(repo)
    doc = repo.for_session(SET_ID, SESSION)
    assert doc is not None
    assert [l["symbol"] for l in doc["lines"]] == ["OLD", "NEW"]


def test_the_key_is_the_set_and_the_session():
    assert BasketRepository.key(SET_ID, SESSION) == f"{SET_ID}:2026-10-01"


def test_accepting_twice_in_a_session_is_refused():
    """A basket records a decision. Silently replacing it would let the
    numbers change under a page someone is reading -- which is exactly what
    storing it prevents."""
    repo = _repo()
    _accept(repo)
    with pytest.raises(AlreadyExists):
        _accept(repo)


def test_a_later_session_is_a_different_basket():
    """Day two of the two-day protocol prices again, and legitimately."""
    repo = _repo()
    _accept(repo)
    _accept(repo, session=date(2026, 10, 2))
    assert repo.for_session(SET_ID, date(2026, 10, 2)) is not None


# ---------------------------------------------------------------- lines ---

def test_prices_are_stored_as_approved():
    """The basket is a statement about these prices; losing them would make
    it a statement about nothing."""
    repo = _repo()
    _accept(repo)
    line = repo.line(SET_ID, SESSION, 2)
    assert line["price"] == Decimal("224.17")


def test_a_line_is_found_by_the_seq_the_tag_is_derived_from():
    """`submission.tag_for` hashes (set_id, seq), so these numbers become
    order identity the moment the row is written."""
    repo = _repo()
    _accept(repo)
    assert repo.line(SET_ID, SESSION, 1)["symbol"] == "OLD"
    assert repo.line(SET_ID, SESSION, 2)["symbol"] == "NEW"


def test_an_unknown_seq_returns_none_rather_than_the_first_line():
    repo = _repo()
    _accept(repo)
    assert repo.line(SET_ID, SESSION, 99) is None


def test_a_line_from_a_session_with_no_basket_is_none():
    assert _repo().line(SET_ID, SESSION, 1) is None


def test_the_order_type_defaults_to_limit_when_absent():
    """MARKET is the deliberate choice for a sell leg; defaulting to it would
    make an omission the riskier option."""
    repo = _repo()
    _accept(repo)
    assert repo.line(SET_ID, SESSION, 2)["order_type"] == "LIMIT"


def test_a_stated_order_type_is_kept():
    repo = _repo()
    _accept(repo)
    assert repo.line(SET_ID, SESSION, 1)["order_type"] == "MARKET"


def test_prices_given_as_strings_do_not_become_floats():
    """These numbers reach an exchange."""
    repo = _repo()
    _accept(repo, lines=[{"seq": 1, "action": "buy", "symbol": "X",
                          "quantity": 1, "price": "2402.75"}])
    assert repo.line(SET_ID, SESSION, 1)["price"] == Decimal("2402.75")


# --------------------------------------------------------------- latest ---

def test_latest_returns_the_most_recent_session():
    repo = _repo()
    _accept(repo, session=date(2026, 10, 1))
    _accept(repo, session=date(2026, 10, 2))
    assert repo.latest("paper_momentum_v1_001")["session"] == "2026-10-02"


def test_latest_is_none_when_nothing_was_ever_accepted():
    assert _repo().latest("paper_momentum_v1_001") is None
