"""Typed settings, resolved once at process start.

Layered, most-specific wins: `settings.base.yaml` -> `settings.<env>.yaml` ->
`ALGOTRADE_*` environment variables. Everything downstream reads the frozen
`Settings` object; nothing reads `os.environ` after this module has run, so
what a job was configured with is inspectable and loggable in one place.

Secrets (`MONGODB_URI`, `TELEGRAM_TOKEN`) come from the environment only --
never from a YAML file that could be committed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

VALID_ENVS: Final[frozenset[str]] = frozenset({"local", "paper", "live"})

#: Separate DATABASES, not a shared one with an `env` field. A forgotten filter
#: would otherwise mix paper and live money, and that must be structurally
#: impossible rather than merely unlikely (design §1.2).
_DEFAULT_DB: Final[dict[str, str]] = {
    "local": "algotrade_local",
    "paper": "algotrade_paper",
    "live": "algotrade_live",
}


class ConfigError(RuntimeError):
    """Raised for a missing or contradictory configuration."""


@dataclass(frozen=True, slots=True)
class Settings:
    env: str
    mongodb_uri: str
    database: str

    telegram_token: str = ""
    telegram_chat: str = ""

    data_dir: Path = Path("data")
    config_dir: Path = Path("config")
    strategy_config: Path = Path("config/strategy/momentum_v1.yaml")
    cost_config: Path = Path("config/costs.yaml")

    #: Charge reserve withheld from investable cash so the last buy does not
    #: fail for want of a few rupees (§5.4).
    charge_reserve_bps: int = 30
    #: A recommendation older than this is acting on stale information.
    recommendation_ttl_days: int = 3

    dry_run: bool = False
    code_commit: str = ""

    @property
    def is_live(self) -> bool:
        return self.env == "live"

    def redacted(self) -> dict[str, Any]:
        """Safe to log: connection strings carry credentials."""
        return {
            "env": self.env,
            "database": self.database,
            "mongodb_uri": _redact_uri(self.mongodb_uri),
            "telegram_configured": bool(self.telegram_token),
            "dry_run": self.dry_run,
            "code_commit": self.code_commit,
        }


def _redact_uri(uri: str) -> str:
    if "@" not in uri:
        return uri
    scheme, _, rest = uri.partition("://")
    _, _, host = rest.partition("@")
    return f"{scheme}://***:***@{host}"


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load(
    env: str | None = None,
    *,
    config_dir: Path | str = "config",
    environ: dict[str, str] | None = None,
) -> Settings:
    """Resolve settings for `env`.

    Args:
        env: Environment name. Defaults to `$ALGOTRADE_ENV`, then `local`.
        config_dir: Where the YAML layers live.
        environ: Environment mapping, injected for tests.
    """
    environ = os.environ if environ is None else environ
    env = env or environ.get("ALGOTRADE_ENV", "local")
    if env not in VALID_ENVS:
        raise ConfigError(f"unknown env {env!r}; expected one of {sorted(VALID_ENVS)}")

    config_dir = Path(config_dir)
    merged: dict[str, Any] = {
        **_load_yaml(config_dir / "settings.base.yaml"),
        **_load_yaml(config_dir / f"settings.{env}.yaml"),
    }

    uri = environ.get("MONGODB_URI", merged.get("mongodb_uri", ""))
    if not uri:
        raise ConfigError(
            "MONGODB_URI is not set. It is a secret and must come from the "
            "environment, never from a committed YAML file."
        )

    database = environ.get("MONGODB_DATABASE") or merged.get(
        "database", _DEFAULT_DB[env]
    )
    # A live job pointed at the paper database (or the reverse) is the most
    # expensive configuration mistake available here, so it is refused rather
    # than trusted.
    expected = _DEFAULT_DB[env]
    if env in ("paper", "live") and database != expected:
        raise ConfigError(
            f"env {env!r} must use database {expected!r}, got {database!r} -- "
            f"refusing to run against another environment's data"
        )

    return Settings(
        env=env,
        mongodb_uri=uri,
        database=database,
        telegram_token=environ.get("TELEGRAM_TOKEN", ""),
        telegram_chat=environ.get("TELEGRAM_CHAT", ""),
        data_dir=Path(merged.get("data_dir", "data")),
        config_dir=config_dir,
        strategy_config=Path(
            merged.get("strategy_config", "config/strategy/momentum_v1.yaml")
        ),
        cost_config=Path(merged.get("cost_config", "config/costs.yaml")),
        charge_reserve_bps=int(merged.get("charge_reserve_bps", 30)),
        recommendation_ttl_days=int(merged.get("recommendation_ttl_days", 3)),
        dry_run=environ.get("ALGOTRADE_DRY_RUN", "").lower() in ("1", "true", "yes"),
        code_commit=environ.get("GITHUB_SHA", environ.get("ALGOTRADE_COMMIT", "")),
    )
