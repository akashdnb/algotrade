"""The Mongo client, shared across requests within a container.

Serverless is the whole risk here. Each request runs in a short-lived function,
and a naive `MongoClient` per request both exhausts Atlas M0's 500-connection
ceiling under any concurrency and adds a TLS handshake (~1-2 s against this
cluster) to every page load.

Three rules make it work, and all three matter:

* **Module-level client.** Created on cold start, reused for the life of the
  container. This is the only reason the dashboard is usable.
* **`maxPoolSize=1`.** One invocation serves one request; a pool of 100 is 100
  idle sockets counted against a shared ceiling.
* **Never `close()` in a handler.** Correct in a script, wrong here -- it
  discards the pool the next request needs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pymongo import MongoClient

from app.settings import Settings, get_settings

if TYPE_CHECKING:
    from pymongo.database import Database

_client: MongoClient | None = None


def get_client(settings: Settings | None = None) -> MongoClient:
    global _client
    if _client is None:
        s = settings or get_settings()
        _client = MongoClient(
            s.mongodb_uri,
            maxPoolSize=1,
            minPoolSize=0,
            serverSelectionTimeoutMS=5_000,
            connectTimeoutMS=5_000,
            # Retryable writes are on by default and are wanted here: a
            # dropped socket mid-insert must not leave a fill half-recorded.
            retryWrites=True,
            appname="algotrade-dashboard",
        )
    return _client


def get_db(settings: Settings | None = None) -> "Database":
    s = settings or get_settings()
    return get_client(s)[s.mongodb_database]


def reset_client() -> None:
    """Drop the cached client. For tests only."""
    global _client
    if _client is not None:
        _client.close()
    _client = None
