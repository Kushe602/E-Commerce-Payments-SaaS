"""Pytest fixtures.

The environment is configured *before* the app is imported so the settings
singleton (built at import time) picks up the test database and pins the
deterministic fake-payments provider — no Stripe keys, no network.

Between tests we reset the schema with a synchronous engine and dispose the
async engine's connection pool with ``close=False``; that discards pooled
aiosqlite connections bound to a previous test's event loop, which is the
classic source of flaky async-SQLite test suites.
"""
import os

os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_cartify.db"
os.environ["SECRET_KEY"] = "test-secret-key-for-ci-only-000000000000"
os.environ["AUTO_SEED"] = "false"
os.environ["FORCE_FAKE_PAYMENTS"] = "true"
os.environ["COOKIE_SECURE"] = "false"

import pytest
from sqlalchemy import create_engine
from starlette.testclient import TestClient

from app.database import Base
from app.database import engine as async_engine
from app.main import app

SYNC_DB_URL = "sqlite:///./test_cartify.db"


@pytest.fixture(autouse=True)
def fresh_db():
    """Give every test an empty schema, isolated from all others."""
    async_engine.sync_engine.dispose(close=False)
    sync_engine = create_engine(SYNC_DB_URL)
    Base.metadata.drop_all(sync_engine)
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    yield


@pytest.fixture
def client():
    """A TestClient with lifespan run (schema init); persists session cookies."""
    with TestClient(app) as test_client:
        yield test_client
