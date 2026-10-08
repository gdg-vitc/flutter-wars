"""Real Postgres connectivity + SQLModel lifecycle (same code path Neon uses).

Gated on SPIKE_DATABASE_URL/DATABASE_URL so a database-less run skips cleanly.
"""

from sqlalchemy.engine import make_url
from sqlmodel import select

from app.infra.db import (
    DatabaseTarget,
    check_connectivity,
    create_db_engine,
    session_scope,
    target_from_settings,
)
from app.infra.infra_probe import InfraProbe, ensure_probe_schema
from app.infra.settings import Settings


def _connect(postgres_url):
    settings = Settings(app_env="test", database_url=postgres_url)
    return settings, create_db_engine(target_from_settings(settings), settings)


def test_connectivity_postgres(postgres_url):
    _, engine = _connect(postgres_url)
    try:
        ok, latency_ms, error_type = check_connectivity(engine)
        assert ok, f"connectivity failed: {error_type}"
        assert latency_ms >= 0
    finally:
        engine.dispose()


def test_sqlmodel_crud_postgres(postgres_url):
    _, engine = _connect(postgres_url)
    try:
        ensure_probe_schema(engine)
        with session_scope(engine) as session:
            session.add(InfraProbe(label="integration"))
        with session_scope(engine) as session:
            rows = session.exec(select(InfraProbe).where(InfraProbe.label == "integration")).all()
        assert len(rows) >= 1
    finally:
        engine.dispose()


def test_unreachable_database_fails_without_leaking_secret():
    secret = "nopass-should-not-appear"
    settings = Settings(
        app_env="test",
        database_url=f"postgresql+psycopg://nouser:{secret}@127.0.0.1:1/nodb",
        db_connect_timeout_seconds=2,
    )
    target = DatabaseTarget(url=make_url(settings.database_url), source="settings")
    engine = create_db_engine(target, settings)
    try:
        ok, _, error_type = check_connectivity(engine)
        assert ok is False
        assert error_type is not None
        assert secret not in error_type
    finally:
        engine.dispose()
