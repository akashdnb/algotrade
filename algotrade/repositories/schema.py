"""Collection schemas and indexes, as data (design §2).

Declarative on purpose. The definitions here are the single source of truth for
what the database enforces, they are testable without a connection, and
`jobs/init_db.py` applies them idempotently -- so "what does production look
like" is answered by reading this file rather than by inspecting a cluster.

Two enforcement choices worth stating:

* **`Decimal128` is required on money fields by the validator**, not merely by
  convention. A `double` written by a stray script is rejected at the database
  rather than discovered later as a rounding discrepancy.
* **Idempotency is expressed as unique indexes**, not as application checks.
  A retried job hits a duplicate-key error; it cannot race its way to a second
  document (design §0.1, §11.5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final

import pymongo

# --------------------------------------------------------------- helpers ----

_MONEY: Final[dict] = {"bsonType": "decimal"}
_DATE_STR: Final[dict] = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
_PERIOD: Final[dict] = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}$"}
_PORTFOLIO_ID: Final[dict] = {
    "bsonType": "string",
    "pattern": r"^(paper|live|shadow)_[a-z][a-z0-9]*_v\d+_\d{3}$",
}


@dataclass(frozen=True, slots=True)
class Index:
    keys: list[tuple[str, int]]
    name: str
    unique: bool = False
    #: Restricts BOTH what the index covers and what uniqueness applies to.
    partial: dict[str, Any] | None = None
    expire_after_seconds: int | None = None

    def kwargs(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.unique:
            out["unique"] = True
        if self.partial is not None:
            out["partialFilterExpression"] = self.partial
        if self.expire_after_seconds is not None:
            out["expireAfterSeconds"] = self.expire_after_seconds
        return out


@dataclass(frozen=True, slots=True)
class Collection:
    name: str
    validator: dict[str, Any]
    indexes: list[Index] = field(default_factory=list)
    #: Documented so retention is reviewable in one place (§2).
    retention: str = "permanent"


ASC: Final[int] = pymongo.ASCENDING
DESC: Final[int] = pymongo.DESCENDING

DAY: Final[int] = 86_400


# ------------------------------------------------------------ portfolios ----

PORTFOLIOS = Collection(
    name="portfolios",
    retention="permanent -- a closed portfolio keeps its history interpretable",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "mode", "strategy", "strategy_version",
                         "strategy_params_hash", "status", "cash", "units"],
            "properties": {
                "_id": _PORTFOLIO_ID,
                "mode": {"enum": ["paper", "live", "shadow"]},
                "strategy": {"bsonType": "string"},
                "strategy_version": {"bsonType": "string"},
                "strategy_params_hash": {"bsonType": "string"},
                "status": {"enum": ["active", "paused", "closed"]},
                "execution_mode": {"enum": ["manual", "auto_paper"]},
                "currency": {"enum": ["INR"]},
                "initial_capital": _MONEY,
                "monthly_contribution": _MONEY,
                "cash": _MONEY,
                "total_contributed": _MONEY,
                "units": _MONEY,
                "contribution_day": {"bsonType": "int", "minimum": 1, "maximum": 28},
                "label": {"bsonType": "string"},
                # The NIFTYBEES half: valued for reporting, NEVER traded. Present
                # only so total-account NAV is reportable and so allocation
                # variants are genuinely comparable rather than the same book at
                # different scale.
                "benchmark_sleeve": {
                    "bsonType": ["object", "null"],
                    "required": ["enabled", "index", "target_weight"],
                    "properties": {
                        "enabled": {"bsonType": "bool"},
                        "index": {"bsonType": "string"},
                        "target_weight": {"bsonType": "double",
                                          "minimum": 0, "maximum": 1},
                        "value": _MONEY,
                        "cost_basis": _MONEY,
                        "last_rebalanced": {"bsonType": ["string", "null"]},
                    },
                },
            },
        }
    },
    indexes=[Index([("mode", ASC), ("status", ASC)], name="mode_status")],
)


# -------------------------------------------------------------- holdings ----

HOLDINGS = Collection(
    name="holdings",
    retention="deleted when quantity reaches zero; history lives in snapshots",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "symbol", "lots"],
            "properties": {
                "_id": {"bsonType": "string"},           # portfolio_id:symbol
                "portfolio_id": _PORTFOLIO_ID,
                "symbol": {"bsonType": "string"},
                "isin": {"bsonType": "string"},
                "realised_pnl": _MONEY,
                # Pre-2026-09-01 name; kept so existing rows stay valid
                # until the next rebuild replaces them.
                "realised_pnl_ytd": _MONEY,
                "lots": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["lot_id", "acquired_on", "quantity", "price"],
                        "properties": {
                            "lot_id": {"bsonType": "string"},
                            "acquired_on": _DATE_STR,
                            "quantity": {"bsonType": "int", "minimum": 1},
                            "price": _MONEY,
                            "charges": _MONEY,
                            "source_set_id": {"bsonType": "string"},
                        },
                    },
                },
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC)], name="portfolio"),
        Index([("portfolio_id", ASC), ("isin", ASC)], name="portfolio_isin"),
    ],
)


# ------------------------------------------------------- recommendations ----

_ITEM = {
    "bsonType": "object",
    "required": ["seq", "action", "symbol", "quantity", "reference_price", "status"],
    "properties": {
        "seq": {"bsonType": "int", "minimum": 1},
        "action": {"enum": ["buy", "sell"]},
        "symbol": {"bsonType": "string"},
        "quantity": {"bsonType": "int", "minimum": 1},
        "reference_price": _MONEY,
        "estimated_charges": _MONEY,
        "status": {"enum": ["pending", "executed", "partial", "skipped", "expired"]},
        "executed_quantity": {"bsonType": "int", "minimum": 0},
        "executed_price": {"bsonType": ["decimal", "null"]},
        "executed_charges": _MONEY,
        "broker_order_id": {"bsonType": "string"},
        "skip_reason": {"bsonType": "string"},
    },
}

RECOMMENDATIONS = Collection(
    name="recommendations",
    retention="permanent -- this is the trade and tax record",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "period", "revision", "status",
                         "as_of_session", "items"],
            "properties": {
                "_id": {"bsonType": "string"},   # portfolio:period:revision
                "portfolio_id": _PORTFOLIO_ID,
                "rebalance_run_id": {"bsonType": "string"},
                "period": _PERIOD,
                "revision": {"bsonType": "int", "minimum": 1},
                "supersedes": {"bsonType": ["string", "null"]},
                "status": {"enum": ["pending", "notified", "approved", "rejected",
                                    "partially_executed", "completed",
                                    "expired", "superseded"]},
                "holds": {"bsonType": "array", "items": {"bsonType": "string"}},
                "turnover_estimate": {"bsonType": ["double", "null"]},
                "as_of_session": _DATE_STR,
                "expires_on": {"bsonType": ["string", "null"]},
                "reference_nav": _MONEY,
                "reference_cash": _MONEY,
                "items": {"bsonType": "array", "items": _ITEM},
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("period", ASC), ("revision", DESC)],
              name="portfolio_period_revision"),
        Index([("portfolio_id", ASC), ("status", ASC)], name="portfolio_status"),
        Index([("status", ASC), ("expires_on", ASC)], name="expiry_sweep"),
        Index([("items.symbol", ASC), ("generated_at", DESC)], name="symbol_history"),
    ],
)


# ---------------------------------------------------- portfolio_snapshots ----

SNAPSHOTS = Collection(
    name="portfolio_snapshots",
    retention="permanent -- ~1 MB per portfolio-year; downsampling would break "
              "exact reconstruction (§9.1)",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "date", "nav", "cash", "units",
                         "unit_nav"],
            "properties": {
                "_id": {"bsonType": "string"},           # portfolio:date
                "portfolio_id": _PORTFOLIO_ID,
                "date": _DATE_STR,
                "nav": _MONEY,
                "cash": _MONEY,
                "invested_value": _MONEY,
                "market_value": _MONEY,
                "invested_capital": _MONEY,
                "realised_pnl": _MONEY,
                "unrealised_pnl": _MONEY,
                "total_pnl": _MONEY,
                "total_return": {"bsonType": ["double", "null"]},
                "day_return": {"bsonType": ["double", "null"]},
                "units": _MONEY,
                "unit_nav": _MONEY,
                "total_contributed": _MONEY,
                "price_date": _DATE_STR,
                "peak_unit_nav": _MONEY,
                "benchmark_close": {"bsonType": ["decimal", "null"]},
                "benchmark_sleeve_value": {"bsonType": ["decimal", "null"]},
                "drawdown": {"bsonType": "double", "maximum": 0},
                "positions": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["symbol", "quantity", "close", "value"],
                        "properties": {
                            "symbol": {"bsonType": "string"},
                            "quantity": {"bsonType": "int"},
                            "close": _MONEY,
                            "value": _MONEY,
                            "weight": {"bsonType": "double"},
                            "unrealised_pnl": _MONEY,
                        },
                    },
                },
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("date", DESC)], name="portfolio_date"),
        Index([("date", DESC)], name="date"),
    ],
)


# -------------------------------------------------------- rebalance_runs ----

REBALANCE_RUNS = Collection(
    name="rebalance_runs",
    retention="permanent -- ~12 documents per portfolio-year",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "period", "status"],
            "properties": {
                "_id": {"bsonType": "string"},           # portfolio:period
                "portfolio_id": _PORTFOLIO_ID,
                "period": _PERIOD,
                "status": {"enum": ["pending", "generating", "generated", "notified",
                                    "executing", "completed", "failed"]},
                "triggered_by": {"enum": ["schedule", "manual", "recovery"]},
                "attempt": {"bsonType": "int", "minimum": 1},
                "as_of_session": _DATE_STR,
                "input_fingerprint": {
                    "bsonType": "object",
                    "required": ["panel_manifest_hash", "strategy_params_hash",
                                 "code_commit"],
                    "properties": {
                        "panel_manifest_hash": {"bsonType": "string"},
                        "strategy_params_hash": {"bsonType": "string"},
                        "cost_model_hash": {"bsonType": "string"},
                        "code_commit": {"bsonType": "string"},
                        "last_session": _DATE_STR,
                        "universe_size": {"bsonType": "int"},
                    },
                },
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("period", DESC)], name="portfolio_period"),
        # Find stuck runs without scanning completed ones.
        Index([("status", ASC)], name="in_flight",
              partial={"status": {"$in": ["pending", "generating", "executing"]}}),
    ],
)


# ------------------------------------------------------------ audit_logs ----

AUDIT_LOGS = Collection(
    name="audit_logs",
    retention="financial actions permanent; operational info entries carry "
              "expires_at and are swept at 90 days",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["ts", "actor", "action", "entity_type", "entity_id"],
            "properties": {
                "ts": {"bsonType": "date"},
                "portfolio_id": {"bsonType": ["string", "null"]},
                "actor": {"enum": ["system", "user", "github-actions"]},
                "action": {"bsonType": "string"},
                "entity_type": {"bsonType": "string"},
                "entity_id": {"bsonType": "string"},
                "severity": {"enum": ["info", "warning", "error"]},
                "expires_at": {"bsonType": ["date", "null"]},
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("ts", DESC)], name="portfolio_ts"),
        Index([("entity_type", ASC), ("entity_id", ASC), ("ts", ASC)],
              name="entity_history"),
        # "the whole life of ONE entity, newest first" -- the dashboard's audit
        # filter. `entity_history` above cannot serve it: that index leads with
        # `entity_type`, which the question does not supply, so the query fell
        # back to scanning `portfolio_ts`. Fine at 46 rows, not at the ~15k a
        # year this collection accumulates.
        Index([("entity_id", ASC), ("ts", DESC)], name="entity_recent"),
        Index([("ts", DESC)], name="recent"),
        Index([("severity", ASC), ("ts", DESC)], name="problems",
              partial={"severity": {"$in": ["warning", "error"]}}),
        # TTL on a NULLABLE field, never on `ts`: a TTL on ts would eventually
        # delete the trade history this collection exists to preserve.
        Index([("expires_at", ASC)], name="ttl_operational", expire_after_seconds=0),
    ],
)


# -------------------------------------------------------------- job_runs ----

JOB_RUNS = Collection(
    name="job_runs",
    retention="TTL 365 days -- telemetry, not a financial record",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "job_name", "business_date", "status", "attempt"],
            "properties": {
                "_id": {"bsonType": "string"},      # job:date:attempt
                "job_name": {"bsonType": "string"},
                "business_date": _DATE_STR,
                "attempt": {"bsonType": "int", "minimum": 1},
                "status": {"enum": ["running", "success", "failed", "skipped"]},
                "skip_reason": {"bsonType": ["string", "null"]},
                "github_run_id": {"bsonType": ["string", "null"]},
                "code_commit": {"bsonType": "string"},
                "expires_at": {"bsonType": ["date", "null"]},
            },
        }
    },
    indexes=[
        # THE exactly-once guarantee: at most one SUCCESS per job per business
        # date. Retries and failures may repeat; a second success cannot insert.
        Index([("job_name", ASC), ("business_date", ASC)], name="one_success",
              unique=True, partial={"status": {"$eq": "success"}}),
        Index([("status", ASC), ("started_at", DESC)], name="status_started"),
        Index([("business_date", DESC)], name="business_date"),
        Index([("expires_at", ASC)], name="ttl", expire_after_seconds=0),
    ],
)


# --------------------------------------------------------- notifications ----

NOTIFICATIONS = Collection(
    name="notifications",
    retention="TTL 180 days",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "kind", "channel", "status"],
            "properties": {
                "_id": {"bsonType": "string"},      # kind:portfolio:date[:hash]
                "kind": {"bsonType": "string"},
                "portfolio_id": {"bsonType": ["string", "null"]},
                "business_date": {"bsonType": ["string", "null"]},
                "channel": {"enum": ["telegram"]},
                "status": {"enum": ["queued", "sent", "failed", "suppressed"]},
                "attempts": {"bsonType": "int", "minimum": 0},
                "expires_at": {"bsonType": ["date", "null"]},
            },
        }
    },
    indexes=[
        Index([("status", ASC), ("business_date", DESC)], name="status_date"),
        Index([("expires_at", ASC)], name="ttl", expire_after_seconds=0),
    ],
)


# ------------------------------------------------------------ executions ----
#
# Separate from the embedded items in `recommendations` for two reasons that
# embedding cannot serve: one instruction may be filled by several orders across
# days, and tax filing queries fills directly ("every sell in FY 2026-27")
# rather than unwinding plans. This collection is the authoritative trade
# record; `recommendations.items` keeps a rollup for display.

EXECUTIONS = Collection(
    name="executions",
    retention="permanent -- the authoritative trade and tax record",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "set_id", "item_seq", "symbol",
                         "action", "quantity", "price", "executed_at"],
            "properties": {
                "_id": {"bsonType": "string"},       # set_id:seq:n
                "portfolio_id": _PORTFOLIO_ID,
                "set_id": {"bsonType": "string"},
                "item_seq": {"bsonType": "int", "minimum": 1},
                "symbol": {"bsonType": "string"},
                "isin": {"bsonType": "string"},
                "action": {"enum": ["buy", "sell"]},
                "quantity": {"bsonType": "int", "minimum": 1},
                "price": _MONEY,
                "charges": _MONEY,
                "reference_price": _MONEY,
                "executed_at": {"bsonType": "date"},
                "executed_on": _DATE_STR,            # business date, for tax queries
                "broker_order_id": {"bsonType": "string"},
                "slippage_bps": {"bsonType": ["double", "null"]},
                "note": {"bsonType": "string"},
                "mode": {"enum": ["manual", "paper"]},
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("executed_on", DESC)], name="portfolio_date"),
        Index([("set_id", ASC), ("item_seq", ASC)], name="set_item"),
        Index([("portfolio_id", ASC), ("symbol", ASC), ("executed_on", ASC)],
              name="symbol_history"),
        # A broker order id is unique when present; a duplicate means the same
        # fill was recorded twice, which is the realistic manual-entry mistake.
        Index([("portfolio_id", ASC), ("broker_order_id", ASC)], name="order_id",
              unique=True,
              partial={"broker_order_id": {"$exists": True, "$gt": ""}}),
    ],
)


# --------------------------------------------------------------- signals ----
#
# Keyed by (strategy_version, date), NOT by portfolio: the ranking is a property
# of the strategy and the market, so every portfolio on the same version shares
# one document instead of storing 30 identical rows each.

SIGNALS = Collection(
    name="signals",
    retention="permanent -- ~1 MB/year, and it is the daily record of what the "
              "strategy saw",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "strategy_version", "date", "rows"],
            "properties": {
                "_id": {"bsonType": "string"},       # strategy_version:date
                "strategy_version": {"bsonType": "string"},
                "date": _DATE_STR,
                "universe_size": {"bsonType": "int"},
                "panel_manifest_hash": {"bsonType": "string"},
                "rows": {
                    "bsonType": "array",
                    "items": {
                        "bsonType": "object",
                        "required": ["rank", "symbol", "feature"],
                        "properties": {
                            "rank": {"bsonType": "int", "minimum": 1},
                            "symbol": {"bsonType": "string"},
                            "isin": {"bsonType": "string"},
                            "feature": {"bsonType": "double"},
                            "close": {"bsonType": ["decimal", "null"]},
                            "adtv": {"bsonType": ["decimal", "null"]},
                        },
                    },
                },
            },
        }
    },
    indexes=[
        Index([("strategy_version", ASC), ("date", DESC)], name="version_date"),
        Index([("date", DESC)], name="date"),
    ],
)


# ------------------------------------------------------ cash_transactions ----
#
# The cash LEDGER. `portfolios.cash` is a cached balance and is never trusted
# without reconciliation -- a mutable running total has no way to explain how it
# got where it is, and no way to detect that it drifted.
#
# Every entry is signed: money in is positive, money out negative. The balance
# is `sum(amount)`, always, and `reconcile.py` asserts the cached field matches.

CASH_TRANSACTIONS = Collection(
    name="cash_transactions",
    retention="permanent -- the ledger IS the cash balance",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "portfolio_id", "date", "kind", "amount"],
            "properties": {
                # Deterministic, so a replayed execution cannot double-count.
                "_id": {"bsonType": "string"},
                "portfolio_id": _PORTFOLIO_ID,
                "date": _DATE_STR,
                "kind": {"enum": [
                    "opening_balance", "contribution", "withdrawal",
                    "buy", "sell", "charges", "dividend", "adjustment",
                ]},
                # SIGNED. Positive increases cash, negative decreases it.
                "amount": _MONEY,
                "balance_after": {"bsonType": ["decimal", "null"]},
                "symbol": {"bsonType": ["string", "null"]},
                "execution_id": {"bsonType": ["string", "null"]},
                "set_id": {"bsonType": ["string", "null"]},
                "note": {"bsonType": "string"},
                "created_at": {"bsonType": "date"},
            },
        }
    },
    indexes=[
        Index([("portfolio_id", ASC), ("date", ASC), ("_id", ASC)],
              name="portfolio_date"),
        Index([("portfolio_id", ASC), ("kind", ASC)], name="portfolio_kind"),
        # One ledger entry per execution -- the guard against double-counting a
        # replayed fill.
        Index([("execution_id", ASC)], name="execution", unique=True,
              partial={"execution_id": {"$type": "string"}}),
    ],
)


# ---------------------------------------------------------- broker ----
#
# The encrypted Kite session token. One row at a time: Kite invalidates every
# access token at 06:00 IST by regulatory requirement, so `expires_at` carries
# that instant and a TTL index deletes the row when it arrives.
#
# The point of the TTL is that "no live row" and "no live token" become the
# same condition. A stored token outliving its validity would produce a 403
# from the broker at the worst possible moment, with nothing in our data
# suggesting why.
#
# `expireAfterSeconds: 0` means "expire AT the date in this field", not "never".

BROKER_SESSIONS: Final[Collection] = Collection(
    name="broker_sessions",
    retention="ephemeral -- deleted by TTL at the token's own expiry",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "broker", "ciphertext", "expires_at",
                         "created_at"],
            "properties": {
                "_id": {"bsonType": "string"},        # broker name
                "broker": {"bsonType": "string"},
                # AES-GCM, base64. NEVER the plaintext token, and never a
                # hash -- the header needs the real value back (design §5).
                "ciphertext": {"bsonType": "string"},
                "expires_at": {"bsonType": "date"},
                "created_at": {"bsonType": "date"},
            },
        }
    },
    indexes=[
        Index([("expires_at", ASC)], name="ttl", expire_after_seconds=0),
    ],
)


# Symbol identity across three namespaces: bhavcopy's NSE symbol, the price
# feed's ticker, and Kite's tradingsymbol plus instrument token.
#
# **Keyed by NSE symbol, not ISIN**, and that is a concession to what can
# actually be obtained. ISIN is the better identity -- it survives a rename and
# an EQ->BE series move -- but it is not always available:
#
#   sec_bhavdata_full_*   the current session format, 2021-06 onward   NO ISIN
#   cm*bhav               2011 -> 2021-06, and no longer published     has ISIN
#   Kite /instruments     the tradingsymbol and token source           NO ISIN
#   Kite /portfolio/holdings                                           has ISIN, held names only
#
# A key that cannot always be computed is not a key. So the symbol is the key,
# the ISIN is an optional field carried wherever it can be found, and matching
# prefers ISIN and falls back to symbol -- see `services/demat.py`.
#
# A wrong mapping is SILENT: it returns a price, just for the wrong instrument.
# Hence stored, reviewed and diffed rather than derived at trade time.

SYMBOL_MAP: Final[Collection] = Collection(
    name="symbol_map",
    retention="permanent -- identity, and the record of when it changed",
    validator={
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", "nse_symbol", "updated_on"],
            "properties": {
                "_id": {"bsonType": "string"},        # NSE symbol
                "nse_symbol": {"bsonType": "string"},
                "isin": {"bsonType": "string"},       # when it can be found
                "feed_ticker": {"bsonType": "string"},
                "kite_tradingsymbol": {"bsonType": "string"},
                "kite_instrument_token": {"bsonType": ["int", "long"]},
                "series": {"bsonType": "string"},     # EQ, BE, ...
                "updated_on": _DATE_STR,
            },
        }
    },
    indexes=[
        # Unique only among rows that HAVE an ISIN. Two symbols sharing one
        # would mean a rename we have not noticed, which must be resolved
        # rather than guessed -- but the many rows without one must not
        # collide with each other on empty string.
        Index([("isin", ASC)], name="isin", unique=True,
              partial={"isin": {"$type": "string", "$gt": ""}}),
        Index([("kite_tradingsymbol", ASC)], name="kite_symbol"),
    ],
)


ALL: Final[tuple[Collection, ...]] = (
    PORTFOLIOS,
    HOLDINGS,
    RECOMMENDATIONS,
    EXECUTIONS,
    SIGNALS,
    CASH_TRANSACTIONS,
    SNAPSHOTS,
    REBALANCE_RUNS,
    AUDIT_LOGS,
    JOB_RUNS,
    NOTIFICATIONS,
    BROKER_SESSIONS,
    SYMBOL_MAP,
)

BY_NAME: Final[dict[str, Collection]] = {c.name: c for c in ALL}

#: Applied at write time by the repositories (see `codec.encode_doc`).
MONEY_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "portfolios": ("initial_capital", "monthly_contribution", "cash",
                   "total_contributed"),
    "holdings": ("realised_pnl", "realised_pnl_ytd"),
    "recommendations": ("reference_nav", "reference_cash"),
    "executions": ("price", "charges", "reference_price"),
    "cash_transactions": ("amount", "balance_after"),
    "signals": (),
    "portfolio_snapshots": ("nav", "cash", "invested_value", "market_value",
                            "invested_capital", "realised_pnl",
                            "unrealised_pnl", "total_pnl", "total_contributed",
                            # Declared late: nothing wrote `benchmark_close`
                            # until 2026-09-01, so it was never decoded --
                            # and `_benchmark_return`, dead code until then,
                            # got a raw Decimal128 and crashed on arithmetic
                            # the first time the field was populated.
                            "benchmark_close", "benchmark_sleeve_value"),
    "rebalance_runs": (),
    "audit_logs": (),
    "job_runs": (),
    "notifications": (),
    "broker_sessions": (),
    "symbol_map": (),
}

#: Decimals that are NOT money and must not be quantized to paise.
#: `units` needs three places and `unit_nav` four: unit accounting exists so a
#: contribution cannot move the unit NAV, and rounding units to paise
#: reintroduces exactly the drift it is there to prevent.
PRECISION_FIELDS: Final[dict[str, dict[str, int]]] = {
    "portfolios": {"units": 3},
    "portfolio_snapshots": {"units": 3, "unit_nav": 4, "peak_unit_nav": 4},
}

#: Money nested inside sub-documents. A `[]` suffix means an array of them.
#: Declared rather than hand-rolled per repository -- three repositories need
#: this, and the one that forgets writes an unencodable Decimal.
NESTED_MONEY_FIELDS: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "portfolios": {"benchmark_sleeve": ("value", "cost_basis")},
    "holdings": {"lots[]": ("price", "charges")},
    "portfolio_snapshots": {
        "positions[]": ("close", "value", "unrealised_pnl"),
    },
}

#: Retention windows the watchdog stamps onto `expires_at`.
TTL_DAYS: Final[dict[str, int]] = {
    "job_runs": 365,
    "notifications": 180,
    "audit_logs_operational": 90,
}
