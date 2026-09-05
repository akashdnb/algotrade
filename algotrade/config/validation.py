"""Fail-fast configuration validation, and the environment safety guards.

Two principles:

**Report everything, then fail.** `validate` collects every problem rather than
raising on the first. Setting up a new environment is when this runs most, and
fixing one variable per attempt is a bad loop.

**Severity depends on the environment.** Telegram missing in `dev` is a note;
in `paper` it means the system cannot tell you anything, which is worse than
not running. The dead-man switch is the same: without it, total failure is
silent (design §10.6).

The safety guards are the part that matters most. They exist to make one
specific accident impossible: **operating a real-money portfolio from a
non-production environment**, or pointing a production job at another
environment's database.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from algotrade.config.settings import (
    DEFAULT_DATABASE,
    PAPER,
    PROD,
    VALID_ENVIRONMENTS,
    VALID_LOG_LEVELS,
    ConfigError,
    Settings,
)
from algotrade.domain import ids


class Severity(StrEnum):
    ERROR = "error"      # refuses to run
    WARNING = "warning"  # runs, but degraded in a way you should know about
    INFO = "info"


@dataclass(frozen=True, slots=True)
class Finding:
    severity: Severity
    key: str
    message: str
    hint: str = ""

    def __str__(self) -> str:
        mark = {"error": "FAIL", "warning": "WARN", "info": "INFO"}[self.severity]
        base = f"[{mark}] {self.key}: {self.message}"
        return f"{base}\n         -> {self.hint}" if self.hint else base


@dataclass(frozen=True, slots=True)
class Report:
    findings: list[Finding]

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def raise_if_invalid(self) -> None:
        if self.ok:
            return
        detail = "\n".join(str(f) for f in self.errors)
        raise ConfigError(f"{len(self.errors)} configuration error(s):\n{detail}")


def _error(key: str, message: str, hint: str = "") -> Finding:
    return Finding(Severity.ERROR, key, message, hint)


def _warn(key: str, message: str, hint: str = "") -> Finding:
    return Finding(Severity.WARNING, key, message, hint)


def _info(key: str, message: str) -> Finding:
    return Finding(Severity.INFO, key, message)


# ------------------------------------------------------------- validators ---


def _check_environment(s: Settings) -> Iterable[Finding]:
    if s.environment not in VALID_ENVIRONMENTS:
        yield _error(
            "ENVIRONMENT",
            f"{s.environment!r} is not one of {list(VALID_ENVIRONMENTS)}",
            "set ENVIRONMENT=dev for local work",
        )
    if s.log_level not in VALID_LOG_LEVELS:
        yield _error("LOG_LEVEL", f"{s.log_level!r} is not a Python log level",
                     f"one of {list(VALID_LOG_LEVELS)}")


def _check_mongo(s: Settings) -> Iterable[Finding]:
    if not s.mongodb_uri:
        yield _error("MONGODB_URI", "not set",
                     "it is a secret; set it in .env locally, GitHub Secrets in CI")
    elif not s.mongodb_uri.startswith(("mongodb://", "mongodb+srv://")):
        yield _error("MONGODB_URI", "does not look like a MongoDB connection string")

    if not s.mongodb_database:
        yield _error("MONGODB_DATABASE", "not set and no default for this environment")
        return

    # THE guard: a production job pointed at another environment's data, or a
    # dev job pointed at production, is the most expensive mistake available.
    expected = DEFAULT_DATABASE.get(s.environment)
    if expected and s.mongodb_database != expected:
        others = {v: k for k, v in DEFAULT_DATABASE.items()}
        if s.mongodb_database in others:
            yield _error(
                "MONGODB_DATABASE",
                f"environment {s.environment!r} is pointed at "
                f"{s.mongodb_database!r}, which belongs to "
                f"{others[s.mongodb_database]!r}",
                f"use {expected!r}, or change ENVIRONMENT",
            )
        else:
            yield _warn(
                "MONGODB_DATABASE",
                f"{s.mongodb_database!r} is not the default {expected!r} for "
                f"{s.environment!r}",
                "intentional for a scratch database; otherwise a typo",
            )


def _check_telegram(s: Settings) -> Iterable[Finding]:
    if s.telegram_configured:
        return
    missing = [
        k for k, v in (("TELEGRAM_BOT_TOKEN", s.telegram_bot_token),
                       ("TELEGRAM_CHAT_ID", s.telegram_chat_id)) if not v
    ]
    if s.environment == PAPER or s.is_prod:
        yield _error(
            "TELEGRAM", f"{', '.join(missing)} not set",
            "Telegram is the operational interface -- without it the system "
            "runs but cannot tell you anything",
        )
    else:
        yield _info("TELEGRAM", "not configured; notifications will be logged only")


def _check_healthchecks(s: Settings) -> Iterable[Finding]:
    if s.healthchecks_configured:
        if not s.healthchecks_ping_url.startswith("https://"):
            yield _warn("HEALTHCHECKS_PING_URL", "is not https")
        return
    if s.environment == PAPER or s.is_prod:
        yield _error(
            "HEALTHCHECKS_PING_URL", "not set",
            "the dead-man switch is the ONLY monitor not hosted on the "
            "infrastructure it watches -- without it, total failure is silent",
        )
    else:
        yield _info("HEALTHCHECKS_PING_URL", "not configured; no heartbeat in dev")


def _check_broker(s: Settings) -> Iterable[Finding]:
    # No order placement exists yet, so these are optional everywhere. They are
    # validated for SHAPE when present so a half-configured broker is caught
    # now rather than on the first live order.
    if not any((s.broker_name, s.broker_api_key, s.broker_api_secret,
                s.broker_access_token, s.broker_refresh_token)):
        yield _info("BROKER", "not configured (no automatic order placement yet)")
        return

    if s.broker_api_key and not s.broker_api_secret:
        yield _error("BROKER_API_SECRET", "set BROKER_API_KEY but not the secret")
    if s.broker_api_secret and not s.broker_api_key:
        yield _error("BROKER_API_KEY", "set BROKER_API_SECRET but not the key")
    if s.broker_configured and not s.broker_name:
        yield _warn("BROKER_NAME", "credentials present but no broker named")
    if s.broker_configured and not s.is_prod:
        yield _warn(
            "BROKER",
            f"live broker credentials present in environment {s.environment!r}",
            "no code places orders yet, but do not leave prod credentials in a "
            "paper environment",
        )


def _check_portfolio(s: Settings) -> Iterable[Finding]:
    if not s.default_portfolio_id:
        if s.environment == PAPER or s.is_prod:
            yield _error("DEFAULT_PORTFOLIO_ID", "not set",
                         "e.g. paper_momentum_v1_001")
        else:
            yield _info("DEFAULT_PORTFOLIO_ID", "not set")
        return

    if not ids.is_valid(s.default_portfolio_id):
        yield _error(
            "DEFAULT_PORTFOLIO_ID",
            f"{s.default_portfolio_id!r} is not a valid portfolio id",
            "format: {paper|live|shadow}_{strategy}_v{n}_{nnn}",
        )
        return

    # THE OTHER guard: real money may only be operated from production.
    pid = ids.parse(s.default_portfolio_id)
    if pid.is_live and not s.is_prod:
        yield _error(
            "DEFAULT_PORTFOLIO_ID",
            f"live portfolio {s.default_portfolio_id!r} in environment "
            f"{s.environment!r}",
            "a live portfolio may only be operated with ENVIRONMENT=prod",
        )
    if s.is_prod and not pid.is_live:
        yield _warn(
            "DEFAULT_PORTFOLIO_ID",
            f"production environment defaulting to a {pid.mode} portfolio",
            "expected during the paper-trading phase; noted so it is deliberate",
        )


#: Jobs that know how to fail on purpose. Anything else is a typo, and a typo
#: that silently does nothing would leave you waiting for a failure that was
#: never going to happen.
FAULT_INJECTABLE: tuple[str, ...] = ("generate_signals",)


def _check_fault_inject(s: Settings) -> Iterable[Finding]:
    """Deliberate faults are a paper-only tool, and never quiet."""
    if not s.fault_inject:
        return
    if s.is_prod:
        yield _error(
            "ALGOTRADE_FAULT_INJECT", f"set to {s.fault_inject!r} in prod",
            "deliberate failure injection is for the acceptance gate; it must "
            "never be armed against real money -- unset it",
        )
        return
    if s.fault_inject not in FAULT_INJECTABLE:
        yield _error(
            "ALGOTRADE_FAULT_INJECT", f"{s.fault_inject!r} is not injectable",
            f"expected one of {', '.join(FAULT_INJECTABLE)}",
        )
        return
    # Loud on purpose. An injector left armed looks exactly like a real,
    # recurring failure, and the whole point of the exercise is being able to
    # tell those apart.
    yield _warn(
        "ALGOTRADE_FAULT_INJECT",
        f"ARMED for {s.fault_inject} -- this run is MEANT to fail",
        "unset the variable once the failure has been recorded, then let the "
        "next SCHEDULED run repair it unaided",
    )


def _check_paths(s: Settings) -> Iterable[Finding]:
    required_dirs = [
        ("data_dir", s.data_dir),
        ("sessions_dir", s.sessions_dir),
    ]
    for name, path in required_dirs:
        if not path.exists():
            yield _warn(name, f"{path} does not exist",
                        "created automatically on first ingestion")

    required_files = [
        ("strategy_config", s.strategy_config),
        ("cost_config", s.cost_config),
    ]
    for name, path in required_files:
        if not path.exists():
            yield _error(name, f"{path} is missing",
                         "the frozen strategy definition and rate card are required")


# ------------------------------------------------------------------ entry ---


def validate(s: Settings) -> Report:
    """Every configuration problem, in one pass. Does no I/O."""
    findings: list[Finding] = []
    for check in (
        _check_environment, _check_mongo, _check_telegram, _check_healthchecks,
        _check_broker, _check_portfolio, _check_fault_inject, _check_paths,
    ):
        findings.extend(check(s))
    return Report(findings)


def require_valid(s: Settings) -> Settings:
    """Validate and raise on any error. The call every job makes at startup."""
    validate(s).raise_if_invalid()
    return s


# ----------------------------------------------------- operational guards ---
#
# Called at the point of action rather than only at startup, because a job can
# be handed a portfolio id on the command line that the startup check never saw.


def guard_portfolio(s: Settings, portfolio_id: str) -> None:
    """Refuse to operate a portfolio that this environment must not touch.

    Raises:
        ConfigError: the id is malformed, or it is a live portfolio outside
            production.
    """
    if not ids.is_valid(portfolio_id):
        raise ConfigError(f"{portfolio_id!r} is not a valid portfolio id")
    pid = ids.parse(portfolio_id)
    if pid.is_live and not s.is_prod:
        raise ConfigError(
            f"refusing to operate live portfolio {portfolio_id!r} from "
            f"environment {s.environment!r} -- real money requires ENVIRONMENT=prod"
        )


def guard_write(s: Settings, *, operation: str) -> None:
    """Refuse a mutating operation in dry-run mode."""
    if s.dry_run:
        raise ConfigError(
            f"{operation} attempted while ALGOTRADE_DRY_RUN is set -- "
            f"dry runs must not write"
        )
