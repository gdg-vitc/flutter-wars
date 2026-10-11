"""Shared test setup for every suite. Runs before any `app` import.

All suites use real PostgreSQL (row locks, CHECKs, triggers and partial indexes must
be real). Each suite's database is wiped, so these URLs must name test databases:

  TEST_DATABASE_URL          migrated schema (Alembic head), shared by foundation/,
                             owners/ and e2e/; dropped and rebuilt once per session
  MODULES_TEST_DATABASE_URL  market/ (Modules G-J), whose fixtures create and drop
                             their own tables; must end in _modules_test
  MIGRATION_DATABASE_URL     market/test_migrations.py; must end in _migration_test
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_test"
)
assert "test" in TEST_DATABASE_URL, "Refusing to run tests against a non-test database"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
for key, value in {
    "APP_NAME": "Flutter Wars backend (tests)",
    "ENVIRONMENT": "test",
    "LOG_LEVEL": "INFO",
    "DATABASE_CONNECT_TIMEOUT_SECONDS": "10",
    "DATABASE_POOL_SIZE": "10",
    "DATABASE_MAX_OVERFLOW": "20",
    "JWT_SECRET_KEY": "test-jwt-secret-not-for-production-0123456789",
    "JWT_ALGORITHM": "HS256",
    "AUTH_ACCESS_TOKEN_MINUTES": "15",
}.items():
    os.environ.setdefault(key, value)

SERVER = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def migrated_schema() -> Iterator[None]:
    """Drop the test schema and apply the whole migration history once."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from app.core.db import get_engine

    with get_engine().begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(SERVER / "alembic.ini"))
    cfg.set_main_option("script_location", str(SERVER / "migrations"))
    command.upgrade(cfg, "head")
    yield
