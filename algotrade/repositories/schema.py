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
                "status": {"enum": ["pending", "notified", "partially_executed",
                                    "completed", "expired", "superseded"]},
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
                "units": _MONEY,
                "unit_nav": _MONEY,
                "total_contributed": _MONEY,
                "peak_unit_nav": _MONEY,
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


ALL: Final[tuple[Collection, ...]] = (
    PORTFOLIOS,
    HOLDINGS,
    RECOMMENDATIONS,
    SNAPSHOTS,
    REBALANCE_RUNS,
    AUDIT_LOGS,
    JOB_RUNS,
    NOTIFICATIONS,
)

BY_NAME: Final[dict[str, Collection]] = {c.name: c for c in ALL}

#: Applied at write time by the repositories (see `codec.encode_doc`).
MONEY_FIELDS: Final[dict[str, tuple[str, ...]]] = {
    "portfolios": ("initial_capital", "monthly_contribution", "cash",
                   "total_contributed", "units"),
    "holdings": ("realised_pnl_ytd",),
    "recommendations": ("reference_nav", "reference_cash"),
    "portfolio_snapshots": ("nav", "cash", "invested_value", "units", "unit_nav",
                            "total_contributed", "peak_unit_nav"),
    "rebalance_runs": (),
    "audit_logs": (),
    "job_runs": (),
    "notifications": (),
}

#: Retention windows the watchdog stamps onto `expires_at`.
TTL_DAYS: Final[dict[str, int]] = {
    "job_runs": 365,
    "notifications": 180,
    "audit_logs_operational": 90,
}
