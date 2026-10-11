import os

import pytest
from fastapi.testclient import TestClient

# tests/conftest.py sets the environment (and the PostgreSQL test URL) first.
from app.core.config import Settings
from app.core.db import reset_database_for_testing
from app.main import create_app


@pytest.fixture(scope="session", autouse=True)
def _schema(migrated_schema) -> None:
    """Module A-C tests run on the migrated schema shared with owners/ and e2e/."""


@pytest.fixture
def settings() -> Settings:
    return Settings(
        APP_NAME="GDG Flutter Workshop Backend",
        ENVIRONMENT="test",
        LOG_LEVEL="INFO",
        DATABASE_URL=os.environ["DATABASE_URL"],
        DATABASE_CONNECT_TIMEOUT_SECONDS=10,
        DATABASE_POOL_SIZE=5,
        DATABASE_MAX_OVERFLOW=10,
        JWT_SECRET_KEY=os.environ["JWT_SECRET_KEY"],
        JWT_ALGORITHM="HS256",
        AUTH_ACCESS_TOKEN_MINUTES=15,
    )


@pytest.fixture
def app(settings: Settings):
    reset_database_for_testing()
    application = create_app(settings)
    yield application
    reset_database_for_testing()


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
