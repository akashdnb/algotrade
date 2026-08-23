"""Integration tests run against a real MongoDB.

Skipped unless `ALGOTRADE_TEST_MONGODB_URI` is set. Deliberately not mocked:
`mongomock` implements neither JSON Schema validation, partial indexes, nor
TTL -- which are exactly the guarantees these tests exist to verify. A green
suite against a mock would prove nothing.

    # local
    docker run -d --rm -p 27017:27017 mongo:8
    export ALGOTRADE_TEST_MONGODB_URI=mongodb://localhost:27017

    # Atlas M0 -- the deployment gate
    export ALGOTRADE_TEST_MONGODB_URI='mongodb+srv://...'
    export ALGOTRADE_TEST_MONGODB_DATABASE=algotrade_itest   # optional

**One database for the session, cleared between tests** -- not a fresh database
per test. Two reasons, both discovered against a real Atlas M0:

* An Atlas database user with `readWrite` **cannot `dropDatabase`**. The
  per-test-database design failed on teardown with
  `user is not allowed to do action [dropDatabase]`, and granting
  `atlasAdmin` to make a test suite pass would be the wrong trade.
* Applying 10 validators and ~30 indexes costs ~24 s against a shared tier.
  Doing that per test would take the suite from seconds to a quarter of an
  hour, and a slow gate is one that stops being run.

Isolation is preserved by deleting every document before each test, which
`readWrite` does permit. Indexes and validators persist, which is what we want
-- they are the thing under test.
"""

from __future__ import annotations

import os

import pytest

URI = os.environ.get("ALGOTRADE_TEST_MONGODB_URI", "")
DATABASE = os.environ.get("ALGOTRADE_TEST_MONGODB_DATABASE", "algotrade_itest")

pytestmark = pytest.mark.skipif(not URI, reason="ALGOTRADE_TEST_MONGODB_URI not set")


def _guard_database_name(name: str) -> None:
    """Refuse to run against a database that holds state worth keeping.

    The suite deletes every document before each test. Pointing it at
    `algotrade_paper` would silently destroy a paper track record, and at
    `algotrade_prod` something worse.

    `algotrade_dev` is deliberately ALLOWED: it is a scratch environment by
    definition, and an Atlas database user is scoped per database -- so on a
    real M0 the dev database is often the only one the test credential can
    `collMod`. Requiring a dedicated test database would mean either a second
    Atlas grant or a broader role, and a gate that is awkward to run is a gate
    that stops being run.
    """
    from algotrade.config.settings import PAPER, PROD, DEFAULT_DATABASE

    protected = {DEFAULT_DATABASE[PAPER], DEFAULT_DATABASE[PROD]}
    if name in protected:
        raise RuntimeError(
            f"refusing to run integration tests against {name!r}: this suite "
            f"deletes every document before each test. Set "
            f"ALGOTRADE_TEST_MONGODB_DATABASE to a scratch database."
        )


@pytest.fixture(scope="session")
def _database():
    """Connect once and apply the schema once."""
    if not URI:
        pytest.skip("ALGOTRADE_TEST_MONGODB_URI not set")

    from algotrade.repositories import client

    _guard_database_name(DATABASE)
    db = client.connect(URI, database=DATABASE)
    try:
        client.apply_schema(db)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"cannot prepare {DATABASE}: {type(exc).__name__}: {exc}")
    yield db
    db.client.close()


@pytest.fixture
def db(_database):
    """A clean database for one test.

    `delete_many({})` rather than dropping: allowed under `readWrite`, and it
    leaves the validators and indexes in place -- which are the subject of
    most of these tests.
    """
    from algotrade.repositories import schema

    for collection in schema.ALL:
        _database[collection.name].delete_many({})
    return _database


@pytest.fixture
def scratch_db(_database):
    """For tests that MUTATE the schema (dropping an index, for example).

    Restores the schema afterwards so a schema-mutating test cannot leave the
    shared database broken for whatever runs next.
    """
    from algotrade.repositories import client, schema

    for collection in schema.ALL:
        _database[collection.name].delete_many({})
    yield _database
    # Clear FIRST, then repair. A document left behind by the test blocks the
    # drop-and-recreate path that `apply_schema` relies on when it cannot
    # collMod -- which would poison the shared database for every later test.
    for collection in schema.ALL:
        _database[collection.name].delete_many({})
    client.apply_schema(_database)
