"""Fakes, so the route tests never need Atlas.

A test suite that requires a live database is a suite that stops being run.
Everything here is a plain dict shaped like the real documents.
"""

from __future__ import annotations

import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from bson.decimal128 import Decimal128

ROOT = Path(__file__).resolve().parents[2]
for path in (str(ROOT), str(ROOT / "dashboard")):
    if path not in sys.path:
        sys.path.insert(0, path)

PORTFOLIO = "paper_momentum_v1_001"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Settings are validated at first use and cached; give every test a
    complete, valid environment and a clean cache."""
    # 240.0.0.0/4 is reserved and unroutable. Deliberately not localhost: a
    # route whose `get_db` escapes patching would otherwise CONNECT on a
    # developer's machine and pass, then fail in CI. It must fail everywhere.
    monkeypatch.setenv("MONGODB_URI", "mongodb://240.0.0.1:27017/")
    monkeypatch.setenv("MONGODB_DATABASE", "algotrade_paper")
    monkeypatch.setenv("PORTFOLIO_ID", PORTFOLIO)
    monkeypatch.setenv("DASHBOARD_USER", "tester")
    monkeypatch.setenv("DASHBOARD_PASSWORD", "correct-horse-battery")
    monkeypatch.delenv("DASHBOARD_ALLOW_WRITES", raising=False)

    # Neutralise the developer's dashboard/.env. Settings fall back to it when
    # a variable is absent from the environment, so without this a test that
    # deliberately unsets something would silently read the real local file --
    # and the suite would pass or fail depending on whether that file exists.
    import app.settings as settings_module
    monkeypatch.setattr(settings_module, "ENV_FILE",
                        Path("/nonexistent/dashboard/.env"))

    from app.settings import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bind_db(monkeypatch, db) -> list[str]:
    """Point every route module's `get_db` at the fake, and say which.

    Each route does `from app.db import get_db`, which binds its OWN name, so
    patching `app.db.get_db` alone leaves an already-imported route calling
    the real connector.

    This walked an enumerated list of eight modules and `app/routes/` had
    grown to eleven, so `/rebalances`, `/audit` and `/history` were reaching a
    live mongod. The suite then passed or failed on whether the developer
    happened to have one running -- the same accident the ENV_FILE override
    above exists to prevent. Walking the package cannot drift behind it.
    """
    import importlib
    import pkgutil

    from app import db as db_module
    from app import routes

    monkeypatch.setattr(db_module, "get_db", lambda settings=None: db)
    bound = []
    for info in pkgutil.iter_modules(routes.__path__):
        module = importlib.import_module(f"{routes.__name__}.{info.name}")
        if hasattr(module, "get_db"):
            monkeypatch.setattr(module, "get_db", lambda settings=None: db)
            bound.append(info.name)
    return bound


def d128(value: str) -> Decimal128:
    return Decimal128(Decimal(value))


def snapshot_doc(day: str = "2026-08-28", **over) -> dict:
    doc = {
        "_id": f"{PORTFOLIO}:{day}",
        "portfolio_id": PORTFOLIO,
        "date": day,
        "price_date": day,
        "nav": d128("201071.20"),
        "cash": d128("556.00"),
        "market_value": d128("200515.20"),
        "invested_value": d128("200515.20"),
        "invested_capital": d128("200000.00"),
        "realised_pnl": d128("0.00"),
        "unrealised_pnl": d128("1071.20"),
        "total_pnl": d128("1071.20"),
        "total_contributed": d128("200000.00"),
        "units": d128("2000.000"),
        "unit_nav": d128("100.5356"),
        "peak_unit_nav": d128("100.5356"),
        "drawdown": -0.0002,
        "day_return": 0.0018,
        "total_return": 0.0054,
        "benchmark_close": None,
        "computed_at": datetime(2026, 8, 28, 20, 5, tzinfo=timezone.utc),
        "positions": [
            {"symbol": "GVT&D", "quantity": 2, "close": d128("4339.20"),
             "value": d128("8678.40"), "weight": 0.043,
             "unrealised_pnl": d128("569.00")},
            {"symbol": "HFCL", "quantity": 30, "close": d128("251.47"),
             "value": d128("7544.10"), "weight": 0.038,
             "unrealised_pnl": d128("819.00")},
        ],
    }
    doc.update(over)
    return doc


def holding_doc(symbol: str = "GVT&D", **over) -> dict:
    doc = {
        "_id": f"{PORTFOLIO}:{symbol}",
        "portfolio_id": PORTFOLIO,
        "symbol": symbol,
        "isin": "INE200A01026",
        "realised_pnl_ytd": d128("0.00"),
        "updated_at": datetime(2026, 8, 28, 20, 5, tzinfo=timezone.utc),
        "lots": [{
            "lot_id": f"{symbol}-1",
            "acquired_on": "2026-08-23",
            "quantity": 2,
            "price": d128("4050.00"),
            "charges": d128("9.82"),
            "source_set_id": f"{PORTFOLIO}:2026-08:r1",
        }],
    }
    doc.update(over)
    return doc


class FakeDeleteResult:
    def __init__(self, deleted_count: int) -> None:
        self.deleted_count = deleted_count


class FakeUpdateResult:
    def __init__(self, upserted_id) -> None:
        self.upserted_id = upserted_id
        self.matched_count = 0 if upserted_id else 1


class FakeCollection:
    def __init__(self, docs: list[dict]) -> None:
        self.docs = docs

    def find_one(self, query=None, projection=None, sort=None):
        rows = self._match(query or {})
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda r: r.get(key) or "", reverse=direction < 0)
        return rows[0] if rows else None

    def find(self, query=None, projection=None):
        return FakeCursor(self._match(query or {}))

    def count_documents(self, query=None, limit=0):
        return len(self._match(query or {}))

    def distinct(self, field, query=None):
        return sorted({d.get(field) for d in self._match(query or {})
                       if d.get(field) is not None})

    def delete_one(self, query=None):
        """Session management needs this; nothing else on the dashboard
        deletes, and nothing here can delete a financial record -- the fake
        holds only what a test put in it."""
        for index, doc in enumerate(self.docs):
            if all(self._field_ok(doc.get(k), v)
                   for k, v in (query or {}).items()):
                del self.docs[index]
                return FakeDeleteResult(1)
        return FakeDeleteResult(0)

    def replace_one(self, query=None, replacement=None, upsert=False):
        for index, doc in enumerate(self.docs):
            if all(self._field_ok(doc.get(k), v)
                   for k, v in (query or {}).items()):
                self.docs[index] = dict(replacement or {})
                return FakeUpdateResult(None)
        if upsert:
            self.docs.append(dict(replacement or {}))
            return FakeUpdateResult((replacement or {}).get("_id"))
        return FakeUpdateResult(None)

    def _match(self, query: dict) -> list[dict]:
        out = []
        for doc in self.docs:
            if all(self._field_ok(doc.get(k), v) for k, v in query.items()):
                out.append(dict(doc))
        return out

    @staticmethod
    def _field_ok(value, want) -> bool:
        if isinstance(want, dict):
            if "$ne" in want and value == want["$ne"]:
                return False
            if "$gte" in want and (value is None or value < want["$gte"]):
                return False
            if "$lt" in want and (value is None or value >= want["$lt"]):
                return False
            return True
        return value == want


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for k, d in reversed(key):
                self.rows.sort(key=lambda r: r.get(k) or "", reverse=d < 0)
        else:
            self.rows.sort(key=lambda r: r.get(key) or "", reverse=direction < 0)
        return self

    def skip(self, n):
        self.rows = self.rows[n:]
        return self

    def limit(self, n):
        if n:
            self.rows = self.rows[:n]
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeDb:
    def __init__(self, **collections: list[dict]) -> None:
        self._c = {name: FakeCollection(docs) for name, docs in collections.items()}

    def __getitem__(self, name: str) -> FakeCollection:
        return self._c.setdefault(name, FakeCollection([]))


@pytest.fixture
def db() -> FakeDb:
    return FakeDb(
        portfolio_snapshots=[snapshot_doc("2026-08-27", nav=d128("200718.01")),
                             snapshot_doc("2026-08-28")],
        holdings=[holding_doc("GVT&D"), holding_doc("HFCL", lots=[{
            "lot_id": "HFCL-1", "acquired_on": "2024-01-10", "quantity": 30,
            "price": d128("224.00"), "charges": d128("5.10"),
            "source_set_id": f"{PORTFOLIO}:2024-01:r1"}])],
        portfolios=[{
            "_id": PORTFOLIO, "cash": d128("556.00"), "units": d128("2000.000"),
            "total_contributed": d128("200000.00"),
            "initial_capital": d128("200000.00"),
            "monthly_contribution": d128("20000.00"), "label": "Paper Momentum 30",
            "strategy_version": "momentum_v1", "status": "active"}],
        executions=[
            {"_id": f"{PORTFOLIO}:2026-08:r3:1:1", "portfolio_id": PORTFOLIO,
             "set_id": f"{PORTFOLIO}:2026-08:r3", "item_seq": 1,
             "symbol": "GVT&D", "action": "buy", "quantity": 2,
             "price": d128("4050.00"), "charges": d128("9.82"),
             "reference_price": d128("4050.10"), "slippage_bps": -0.2,
             "executed_on": "2026-08-20", "broker_order_id": ""},
            {"_id": f"{PORTFOLIO}:2026-08:r3:2:1", "portfolio_id": PORTFOLIO,
             "set_id": f"{PORTFOLIO}:2026-08:r3", "item_seq": 2,
             "symbol": "HFCL", "action": "buy", "quantity": 30,
             "price": d128("224.00"), "charges": d128("5.10"),
             "reference_price": d128("224.00"), "slippage_bps": 0.0,
             "executed_on": "2026-08-20", "broker_order_id": "OID-1"},
            # Deliberately in the PREVIOUS financial year: 31 March is the
            # last day of FY 2025, 1 April begins FY 2026.
            {"_id": f"{PORTFOLIO}:2026-03:r1:1:1", "portfolio_id": PORTFOLIO,
             "set_id": f"{PORTFOLIO}:2026-03:r1", "item_seq": 1,
             "symbol": "OLDCO", "action": "sell", "quantity": 5,
             "price": d128("100.00"), "charges": d128("1.00"),
             "reference_price": d128("101.00"), "slippage_bps": -99.0,
             "executed_on": "2026-03-31", "broker_order_id": ""},
        ],
        job_runs=[
            {"_id": "generate_signals:2026-08-28:1", "job_name": "generate_signals",
             "business_date": "2026-08-28", "status": "success", "trigger": "schedule"},
            {"_id": "value_portfolio:2026-08-19:1", "job_name": "value_portfolio",
             "business_date": "2026-08-19", "status": "failed",
             "trigger": "", "synthetic": True},
        ],
    )


@pytest.fixture
def client(db, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    _bind_db(monkeypatch, db)
    return TestClient(create_app())


@pytest.fixture
def client_disabled(db, monkeypatch):
    """The app as Vercel builds it: neither optional route switched on."""
    from fastapi.testclient import TestClient

    monkeypatch.delenv("REVIEW_ENABLED", raising=False)
    monkeypatch.delenv("EXECUTE_ENABLED", raising=False)
    from app.main import create_app
    from app.settings import get_settings
    get_settings.cache_clear()
    _bind_db(monkeypatch, db)
    return TestClient(create_app())


@pytest.fixture
def auth() -> tuple[str, str]:
    return ("tester", "correct-horse-battery")


@pytest.fixture
def two_portfolios(db) -> FakeDb:
    """A shadow book beside the momentum one, as PROJECT.md item 3 anticipates.

    Both live in the SAME database: portfolios are documents, and it is the
    environment (paper vs prod) that is a separate database, never the
    portfolio.
    """
    db["portfolios"].docs.append({
        "_id": "shadow_momentum_v1_001", "label": "Shadow Momentum",
        "cash": d128("0.00"), "units": d128("1000.000"),
        "total_contributed": d128("100000.00"),
        "strategy_version": "momentum_v1", "status": "active", "mode": "shadow",
    })
    db["portfolio_snapshots"].docs.append(
        snapshot_doc("2026-08-28", _id="shadow_momentum_v1_001:2026-08-28",
                     portfolio_id="shadow_momentum_v1_001", nav=d128("99500.00")))
    return db
