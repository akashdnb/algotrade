"""Integration tests run against a real MongoDB.

Skipped unless `ALGOTRADE_TEST_MONGODB_URI` is set. They are deliberately not
mocked: `mongomock` does not implement JSON Schema validation, partial indexes,
or TTL -- which are exactly the guarantees these tests exist to verify. A green
suite against a mock would prove nothing about the real database.

    # local
    docker run -d -p 27017:27017 mongo:8
    export ALGOTRADE_TEST_MONGODB_URI=mongodb://localhost:27017

    # or a real M0, which is what the Phase 1 gate requires
    export ALGOTRADE_TEST_MONGODB_URI='mongodb+srv://...'
"""

from __future__ import annotations

import os
import uuid

import pytest

URI = os.environ.get("ALGOTRADE_TEST_MONGODB_URI", "")

pytestmark = pytest.mark.skipif(not URI, reason="ALGOTRADE_TEST_MONGODB_URI not set")


@pytest.fixture
def db():
    """A throwaway database per test, dropped afterwards.

    Per-test rather than per-session: these tests assert on unique-index
    violations and document counts, and shared state between them would make
    failures depend on execution order.
    """
    if not URI:
        pytest.skip("ALGOTRADE_TEST_MONGODB_URI not set")

    from algotrade.repositories import client

    name = f"algotrade_test_{uuid.uuid4().hex[:12]}"
    database = client.connect(URI, database=name)
    try:
        client.apply_schema(database)
        yield database
    finally:
        database.client.drop_database(name)
        database.client.close()
