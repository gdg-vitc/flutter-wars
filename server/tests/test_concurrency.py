"""Local concurrency behavior of the connection/session abstraction.

Real Worker concurrency (c=25/50/100) is measured by scripts/loadtest.py against a
running Worker; these tests exercise the same lifecycle from threads locally.
"""

from concurrent.futures import ThreadPoolExecutor

from sqlmodel import select

from app.infra.db import check_connectivity, create_db_engine, session_scope, target_from_settings
from app.infra.infra_probe import InfraProbe, ensure_probe_schema
from app.infra.settings import Settings


def _engine(postgres_url):
    settings = Settings(app_env="test", database_url=postgres_url)
    return settings, create_db_engine(target_from_settings(settings), settings)


def test_concurrent_inserts_against_postgres(postgres_url):
    _, engine = _engine(postgres_url)
    try:
        ensure_probe_schema(engine)
        with session_scope(engine) as session:
            before = len(session.exec(select(InfraProbe)).all())

        def write(i: int) -> None:
            with session_scope(engine) as session:
                session.add(InfraProbe(label=f"conc-{i}"))

        with ThreadPoolExecutor(max_workers=25) as pool:
            list(pool.map(write, range(25)))

        with session_scope(engine) as session:
            after = len(session.exec(select(InfraProbe)).all())
        assert after - before == 25
    finally:
        engine.dispose()


def test_per_request_engines_are_safe_concurrently(postgres_url):
    """Mirrors the Worker pattern: a fresh NullPool engine per unit of work."""

    def one(_: int) -> bool:
        settings, engine = _engine(postgres_url)
        try:
            ok, _, _ = check_connectivity(engine)
            return ok
        finally:
            engine.dispose()

    with ThreadPoolExecutor(max_workers=25) as pool:
        results = list(pool.map(one, range(25)))
    assert all(results), "every per-request engine should connect successfully"
