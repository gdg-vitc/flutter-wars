"""Readiness hook behavior, including failure paths and secret safety."""

from app.infra.health import check_database, readiness
from app.infra.settings import Settings


def test_readiness_fails_closed_without_configuration():
    ok, payload = readiness(None, Settings(app_env="development", database_url=None))
    assert ok is False
    assert payload["status"] == "not_ready"
    dependency = payload["dependencies"][0]
    assert dependency["ok"] is False
    assert dependency["error_type"] == "ConfigError"


def test_readiness_reports_unreachable_database_without_leaking_secret():
    secret = "leaky-secret-value"
    settings = Settings(
        app_env="test",
        database_url=f"postgresql+psycopg://u:{secret}@127.0.0.1:1/db",
        db_connect_timeout_seconds=2,
    )
    status = check_database(None, settings)
    assert status.ok is False
    assert status.error_type is not None
    assert secret not in str(status.to_dict())
    # No SQL or connection string in the payload.
    assert "postgresql" not in str(status.to_dict())


def test_readiness_ok_against_postgres(postgres_url):
    status = check_database(None, Settings(app_env="test", database_url=postgres_url))
    assert status.ok is True
    assert status.source == "settings"
    assert status.latency_ms is not None and status.latency_ms >= 0
    assert status.error_type is None
