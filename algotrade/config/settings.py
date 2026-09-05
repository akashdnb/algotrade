"""Typed settings, resolved once at process start.

Precedence, most specific wins: **process environment** -> `.env` file (dev
convenience only) -> built-in defaults. Everything downstream reads the frozen
`Settings` object; nothing reads `os.environ` afterwards, so what a job was
configured with is inspectable and loggable in one place.

Secrets never come from a committed file. `.env` is gitignored and is a local
development convenience; GitHub Actions supplies the same names as Secrets.

The `.env` parser here is ~20 lines rather than a `python-dotenv` dependency:
one file, one format, no interpolation, and no expression evaluation -- which
is also one less way for a config file to execute something.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

Environment = str

DEV: Final[str] = "dev"
PAPER: Final[str] = "paper"
PROD: Final[str] = "prod"

VALID_ENVIRONMENTS: Final[tuple[str, ...]] = (DEV, PAPER, PROD)

#: Separate DATABASES per environment, not a shared one with an `env` field.
#: A forgotten filter would otherwise mix paper and real money, and that must be
#: structurally impossible rather than merely unlikely.
DEFAULT_DATABASE: Final[dict[str, str]] = {
    DEV: "algotrade_dev",
    PAPER: "algotrade_paper",
    PROD: "algotrade_prod",
}

VALID_LOG_LEVELS: Final[tuple[str, ...]] = (
    "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL",
)

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


class ConfigError(RuntimeError):
    """Configuration is missing, malformed, or internally contradictory."""


# ------------------------------------------------------------ .env loading ---


def parse_env_file(text: str) -> dict[str, str]:
    """Parse `KEY=VALUE` lines. No interpolation, no `export`, no evaluation.

    Blank lines and `#` comments are ignored. Surrounding single or double
    quotes are stripped. A line without `=` is ignored rather than raising --
    a stray note in a local file should not stop a job.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            out[key] = value
    return out


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return parse_env_file(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- settings ---


@dataclass(frozen=True, slots=True)
class Settings:
    """Everything a job needs, resolved and validated.

    Frozen: a job's configuration cannot change mid-run, which means a log line
    written at the start describes the whole run.
    """

    environment: str
    log_level: str

    mongodb_uri: str
    mongodb_database: str

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    broker_name: str = ""
    broker_api_key: str = ""
    broker_api_secret: str = ""
    broker_access_token: str = ""
    broker_refresh_token: str = ""
    #: base64 AES-256 key encrypting the stored session token. Held outside the
    #: database, so a leaked dump yields ciphertext and nothing usable.
    broker_token_key: str = ""

    healthchecks_ping_url: str = ""
    #: Overrides for setups using distinct checks. Empty means "derive from
    #: the base", which is how healthchecks.io actually works.
    healthchecks_start_url: str = ""
    healthchecks_success_url: str = ""
    healthchecks_fail_url: str = ""

    default_portfolio_id: str = ""

    # --- paths (not secrets; defaulted from the repo layout) ---------------
    repo_root: Path = REPO_ROOT
    data_dir: Path = REPO_ROOT / "data"
    sessions_dir: Path = REPO_ROOT / "data" / "sessions"
    archive_dir: Path = REPO_ROOT / "data" / "archive"
    curated_dir: Path = REPO_ROOT / "data" / "curated"
    indices_dir: Path = REPO_ROOT / "data" / "indices"
    strategy_config: Path = REPO_ROOT / "config" / "strategy" / "momentum_v1.yaml"
    cost_config: Path = REPO_ROOT / "config" / "costs.yaml"

    # --- behaviour ---------------------------------------------------------
    charge_reserve_bps: int = 30
    recommendation_ttl_days: int = 3
    #: Sessions of history the production path needs. 273 for momentum, 20 for
    #: the universe window, and the rest is corporate-action adjustment
    #: warm-up -- below ~340 the signal silently DIVERGES from the validated
    #: pipeline rather than failing. See services/panel.MIN_SESSIONS.
    min_history_sessions: int = 400
    dry_run: bool = False
    code_commit: str = ""
    github_run_id: str = ""
    #: `schedule`, `workflow_dispatch`, ... Stamped on every job run so that
    #: "recovered with no manual step" is a fact the ledger can be queried for
    #: rather than an assumption the verifier makes. Empty outside CI.
    github_event_name: str = ""
    #: For `repository_dispatch`, what the dispatcher said it was: `vercel-cron`
    #: for the scheduled backup trigger, `manual` for a human calling the
    #: endpoint. The event name alone cannot tell those apart, and the
    #: difference decides whether a repair counts as unaided.
    github_trigger_source: str = ""

    #: Deliberate fault injection, for the acceptance gate's self-healing
    #: criterion. `<job_name>` -- currently only `generate_signals`.
    #:
    #: The property being claimed is that a FAILED run repairs itself unaided.
    #: Nothing else can produce that on demand: breaking `MONGODB_URI` kills
    #: the config check before any job starts, and the job ledger lives in the
    #: database you just disconnected, so no failure is recorded at all.
    #: Deleting session files is repaired silently by the next ingest, which
    #: never fails. A controlled fault is the only honest way to exercise the
    #: real path -- claim, raise, record, alert, heartbeat -- end to end.
    #:
    #: **Refused outright in prod** by `validation`; see `_check_fault_inject`.
    fault_inject: str = ""

    # ---------------------------------------------------------- predicates --

    @property
    def archive_dirs(self) -> list[Path]:
        """Every directory holding already-fetched sessions, newest tier first.

        `data/archive` is the tiered frozen archive (restored from a Release
        asset in CI). The two `data/raw` paths are the pre-existing research
        mirror and its backfill, kept so the tooling works on a developer
        machine without a migration step. Only directories that exist are
        returned, so a CI runner with only the Release asset is unaffected.
        """
        candidates = [
            self.archive_dir,
            self.data_dir / "raw" / "nse-data-bank" / "data",
            self.data_dir / "raw" / "backfill",
        ]
        return [p for p in candidates if p.exists()]

    @property
    def is_prod(self) -> bool:
        return self.environment == PROD

    @property
    def is_dev(self) -> bool:
        return self.environment == DEV

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def broker_configured(self) -> bool:
        return bool(self.broker_api_key and self.broker_api_secret)

    @property
    def healthchecks_configured(self) -> bool:
        return bool(self.healthchecks_ping_url or self.healthchecks_success_url)

    def redacted(self) -> dict[str, Any]:
        """Safe to log. Connection strings and tokens carry credentials."""
        return {
            "environment": self.environment,
            "log_level": self.log_level,
            "mongodb_uri": redact_uri(self.mongodb_uri),
            "mongodb_database": self.mongodb_database,
            "default_portfolio_id": self.default_portfolio_id,
            "telegram_configured": self.telegram_configured,
            "broker_configured": self.broker_configured,
            "broker_name": self.broker_name or None,
            "healthchecks_configured": self.healthchecks_configured,
            "dry_run": self.dry_run,
            "code_commit": self.code_commit[:8] if self.code_commit else None,
        }


def redact_uri(uri: str) -> str:
    """Strip credentials from a connection string for logging."""
    if not uri or "@" not in uri:
        return uri
    scheme, sep, rest = uri.partition("://")
    if not sep:
        return "***"
    _, _, host = rest.rpartition("@")
    return f"{scheme}://***:***@{host}"


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def load(
    *,
    environ: dict[str, str] | None = None,
    env_file: Path | None = None,
    repo_root: Path | None = None,
) -> Settings:
    """Resolve settings. Does NOT validate -- see `validation.validate`.

    Separating the two means `check_config` can report *every* problem at once
    instead of dying on the first, which is what makes it useful for setting up
    a new environment.

    Args:
        environ: Process environment. Injected for tests.
        env_file: `.env` to fall back to. Defaults to `<repo>/.env`.
        repo_root: Overrides the inferred repository root.
    """
    environ = dict(os.environ if environ is None else environ)
    root = repo_root or REPO_ROOT
    file_values = load_env_file(env_file if env_file is not None else root / ".env")

    def get(key: str, default: str = "") -> str:
        # Process environment wins: CI sets real Secrets, and a stale local
        # .env must never silently override them.
        return environ.get(key) or file_values.get(key) or default

    environment = get("ENVIRONMENT", DEV).strip().lower()
    database = get("MONGODB_DATABASE") or DEFAULT_DATABASE.get(environment, "")

    return Settings(
        environment=environment,
        log_level=get("LOG_LEVEL", "INFO").strip().upper(),
        mongodb_uri=get("MONGODB_URI"),
        mongodb_database=database,
        telegram_bot_token=get("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get("TELEGRAM_CHAT_ID"),
        broker_name=get("BROKER_NAME"),
        broker_api_key=get("BROKER_API_KEY"),
        broker_api_secret=get("BROKER_API_SECRET"),
        broker_access_token=get("BROKER_ACCESS_TOKEN"),
        broker_refresh_token=get("BROKER_REFRESH_TOKEN"),
        broker_token_key=get("BROKER_TOKEN_KEY"),
        healthchecks_ping_url=get("HEALTHCHECKS_PING_URL"),
        healthchecks_start_url=get("HEALTHCHECKS_START_URL"),
        healthchecks_success_url=get("HEALTHCHECKS_SUCCESS_URL"),
        healthchecks_fail_url=get("HEALTHCHECKS_FAIL_URL"),
        default_portfolio_id=get("DEFAULT_PORTFOLIO_ID"),
        repo_root=root,
        data_dir=root / "data",
        sessions_dir=root / "data" / "sessions",
        archive_dir=root / "data" / "archive",
        curated_dir=root / "data" / "curated",
        indices_dir=root / "data" / "indices",
        strategy_config=root / "config" / "strategy" / "momentum_v1.yaml",
        cost_config=root / "config" / "costs.yaml",
        charge_reserve_bps=int(get("CHARGE_RESERVE_BPS", "30")),
        recommendation_ttl_days=int(get("RECOMMENDATION_TTL_DAYS", "3")),
        dry_run=_flag(get("ALGOTRADE_DRY_RUN")),
        code_commit=get("GITHUB_SHA") or get("ALGOTRADE_COMMIT"),
        github_run_id=get("GITHUB_RUN_ID"),
        github_event_name=get("GITHUB_EVENT_NAME"),
        github_trigger_source=get("ALGOTRADE_TRIGGER_SOURCE"),
        fault_inject=get("ALGOTRADE_FAULT_INJECT").strip(),
    )
