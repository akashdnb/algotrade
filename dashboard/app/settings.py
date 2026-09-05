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
from pathlib import Path

#: Local development convenience only. On a deployment the platform supplies
#: these, and the process environment always wins -- a stale local file must
#: never silently override what the host was configured with.
ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

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

    #: The pre-execution review screen. OFF by default, and off on Vercel.
    #:
    #: Not a preference. Pricing thirty symbols takes ~27s against the
    #: function's 30s limit, and a rebalance month prices forty to fifty; and
    #: the page reads `config/costs.yaml` and the strategy spec, which
    #: `.vercelignore` deliberately keeps off the deploy. Enable it wherever
    #: the process is long-lived and the repository is present.
    review_enabled: bool = False

    #: Reading the demat. Credentials only -- this deployment cannot place an
    #: order, and not merely because no code does: Zerodha requires a
    #: registered static IP for order placement and serverless egress has none.
    broker_name: str = ""
    broker_api_key: str = ""
    #: Needed only to sign the token exchange. Never sent anywhere, and never
    #: put in a URL -- it leaves this process solely as a SHA-256 digest.
    broker_api_secret: str = ""
    broker_token_key: str = ""

    @property
    def is_prod(self) -> bool:
        return self.mongodb_database == "algotrade_prod"

    @property
    def broker_readable(self) -> bool:
        """Enough configuration to attempt a read. Never enough to write."""
        return bool(self.broker_api_key and self.broker_token_key)

    @property
    def broker_login_ready(self) -> bool:
        """Enough to complete the OAuth handshake.

        Stricter than `broker_readable`: the exchange also needs the secret, to
        sign the checksum. Separated so the page can say which piece is
        missing instead of offering a login that will fail at the last step.
        """
        return bool(self.broker_api_key and self.broker_api_secret
                    and self.broker_token_key)

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


def _read_env_file(path: Path) -> dict[str, str]:
    """`KEY=VALUE` lines. No interpolation, no `export`, no evaluation --
    which is also one fewer way for a config file to execute something."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.strip():
            out[key.strip()] = value
    return out


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    from_file = _read_env_file(ENV_FILE)

    def get(key: str, default: str = "") -> str:
        # Process environment first: on Vercel that is the only source, and
        # locally it lets a one-off override beat the file.
        return (os.environ.get(key) or from_file.get(key) or default).strip()

    def need(key: str) -> str:
        value = get(key)
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

    allow_writes = _flag(get("DASHBOARD_ALLOW_WRITES"))

    return Settings(
        mongodb_uri=need("MONGODB_URI"),
        mongodb_database=database,
        portfolio_id=need("PORTFOLIO_ID"),
        auth_user=need("DASHBOARD_USER"),
        auth_password=password,
        strategy_version=get("STRATEGY_VERSION", "momentum_v1"),
        allow_writes=allow_writes,
        review_enabled=_flag(get("REVIEW_ENABLED")),
        broker_name=get("BROKER_NAME"),
        broker_api_key=get("BROKER_API_KEY"),
        broker_api_secret=get("BROKER_API_SECRET"),
        broker_token_key=get("BROKER_TOKEN_KEY"),
    )
