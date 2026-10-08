import os

import pytest
from fastapi.testclient import TestClient

# The ASGI module exposes ``app`` for Uvicorn and therefore validates its
# environment at import time. Tests provide an isolated local URL first.
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-not-for-production")
os.environ.setdefault("JWT_ALGORITHM", "HS256")
os.environ.setdefault("JWT_ACCESS_TOKEN_MINUTES", "60")

from app.core.config import Settings
from app.core.db import reset_database_for_testing
from app.main import create_app


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
        JWT_SECRET_KEY=os.environ.get(
            "JWT_SECRET_KEY", "test-jwt-secret-not-for-production"
        ),
        JWT_ALGORITHM="HS256",
        JWT_ACCESS_TOKEN_MINUTES=60,
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
