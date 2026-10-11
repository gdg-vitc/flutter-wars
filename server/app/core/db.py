"""Shared SQLModel engine and transaction-scoped session dependency."""

from collections.abc import Generator
from typing import Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from app.core.config import Settings, get_settings

_engine: Engine | None = None
_session_factory: Callable[[], Session] | None = None


def build_engine(settings: Settings) -> Engine:
    """Create a SQLModel-compatible engine for local, Neon, or Hyperdrive URLs."""
    connect_args: dict[str, object] = {}
    if settings.database_dsn == "sqlite://":
        # The test client serves requests on another thread; preserve one
        # in-memory database across those sessions.
        connect_args["check_same_thread"] = False
    if not settings.database_dsn.startswith("sqlite"):
        connect_args["connect_timeout"] = settings.database_connect_timeout_seconds
    options: dict[str, object] = {"pool_pre_ping": True, "connect_args": connect_args}
    if not settings.database_dsn.startswith("sqlite"):
        options["pool_size"] = settings.database_pool_size
        options["max_overflow"] = settings.database_max_overflow
    if settings.database_dsn == "sqlite://":
        options["poolclass"] = StaticPool
    return create_engine(settings.database_dsn, **options)


def configure_database(settings: Settings | None = None) -> Engine:
    """Configure the process database engine; primarily called by application bootstrap."""
    global _engine, _session_factory
    if _engine is None:
        _engine = build_engine(settings or get_settings())
        engine = _engine

        def session_factory() -> Session:
            return Session(engine)

        _session_factory = session_factory
    return _engine


def configure_engine(url: str, **kwargs: object) -> Engine:
    """Use this engine instead of one built from Settings.

    For runtimes whose connection details arrive with the first request, such as
    a Cloudflare Worker's Hyperdrive binding (see server/cloudflare/).
    """
    global _engine, _session_factory
    engine = create_engine(url, **kwargs)  # type: ignore[arg-type]
    _engine = engine

    def session_factory() -> Session:
        return Session(engine)

    _session_factory = session_factory
    return engine


def engine_configured() -> bool:
    return _engine is not None


def get_engine() -> Engine:
    return configure_database()


def session_factory() -> Session:
    """A new session on the configured engine (for owner-managed transactions)."""
    configure_database()
    assert _session_factory is not None
    return _session_factory()


def get_db() -> Generator[Session, None, None]:
    """Yield one transaction-bound session and guarantee rollback/close on failure."""
    factory = _session_factory
    if factory is None:
        configure_database()
        factory = _session_factory
    assert factory is not None
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_database_connection() -> None:
    """Test the configured path without revealing implementation details to callers."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))


def reset_database_for_testing() -> None:
    """Dispose global engine between isolated tests; not for application use."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None


# Name used by modules written before Module A landed.
get_session = get_db
