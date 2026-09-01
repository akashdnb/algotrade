"""Configuration, resolved once per container and validated on first use.

`get_settings()` raising turns a missing variable into a startup failure rather
than a 500 half-way through a page. On a serverless host that is the difference
between a deployment that visibly refuses and a dashboard that renders a blank
NAV as though it were a fact.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

#: Deliberately excludes `algotrade_dev`. A dashboard pointed at the wrong
#: environment shows the wrong money with complete confidence, which is worse
#: than showing nothing at all.
ALLOWED_DATABASES = ("algotrade_paper", "algotrade_prod")

MIN_PASSWORD_LENGTH = 12


class ConfigError(RuntimeError):
    """Configuration is missing, malformed, or internally contradictory."""


@dataclass(frozen=True, slots=True)
class Settings:
    mongodb_uri: str
    mongodb_database: str
    portfolio_id: str
    auth_user: str
    auth_password: str
    strategy_version: str = "momentum_v1"

    #: Recording executions mutates the book. Off by default: a dashboard that
    #: can only read cannot corrupt anything, and the write path should be
    #: switched on deliberately rather than inherited from a default.
    allow_writes: bool = False

    @property
    def is_prod(self) -> bool:
        return self.mongodb_database == "algotrade_prod"

    def redacted(self) -> dict[str, object]:
        """Safe to log. The URI carries a credential."""
        return {
            "database": self.mongodb_database,
            "host": redact_uri(self.mongodb_uri),
            "portfolio_id": self.portfolio_id,
            "strategy_version": self.strategy_version,
            "allow_writes": self.allow_writes,
        }


def redact_uri(uri: str) -> str:
    """Host only, never the credential.

    Written as an explicit parse rather than `rpartition("@")`: that returns
    the *whole string* when there is no `@`, so a URI without credentials
    would have been logged in full. Atlas URIs always carry one, which is
    exactly why that bug would have survived until the day it mattered.
    """
    if "://" not in uri:
        return "***"
    _, _, rest = uri.partition("://")
    authority = rest.split("/", 1)[0].split("?", 1)[0]
    host = authority.rpartition("@")[2] if "@" in authority else authority
    return host or "***"


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    def need(key: str) -> str:
        value = os.environ.get(key, "").strip()
        if not value:
            raise ConfigError(f"{key} is not set")
        return value

    database = need("MONGODB_DATABASE")
    if database not in ALLOWED_DATABASES:
        raise ConfigError(
            f"unexpected database {database!r}; expected one of "
            f"{', '.join(ALLOWED_DATABASES)}"
        )

    password = need("DASHBOARD_PASSWORD")
    if len(password) < MIN_PASSWORD_LENGTH:
        # The deployment URL is guessable and every page shows holdings, NAV
        # and broker order ids.
        raise ConfigError(
            f"DASHBOARD_PASSWORD must be at least {MIN_PASSWORD_LENGTH} characters"
        )

    allow_writes = _flag(os.environ.get("DASHBOARD_ALLOW_WRITES", ""))

    return Settings(
        mongodb_uri=need("MONGODB_URI"),
        mongodb_database=database,
        portfolio_id=need("PORTFOLIO_ID"),
        auth_user=need("DASHBOARD_USER"),
        auth_password=password,
        strategy_version=os.environ.get("STRATEGY_VERSION", "momentum_v1"),
        allow_writes=allow_writes,
    )
