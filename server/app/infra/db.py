"""Database connectivity abstraction (Module M).

Provides the engine/session factory that Foundation's ``get_db()`` will wrap.
Module M does NOT define ``get_db()`` itself; it supplies the connection
lifecycle and the Hyperdrive/local target resolution.

Design (validated by the Phase 1 spike in the real Workers runtime):
- Synchronous SQLModel/SQLAlchemy (async ORMs need greenlet, unavailable in Workers).
- psycopg (sync) driver.
- One engine per request with NullPool: Hyperdrive owns pooling and the Worker
  does not hold sockets between requests. No shared mutable ORM/session state.
- No global asyncio.Lock: Cloudflare's doc suggests one, but measurement showed it
  starves the event loop and the runtime cancels concurrent requests. See
  docs/database-architecture.md ("Concurrency decision").
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.pool import NullPool
from sqlmodel import Session

from app.infra.settings import HYPERDRIVE_BINDING, ConfigError, Settings, redact_database_url

TargetSource = Literal["hyperdrive", "settings"]


@dataclass(frozen=True)
class DatabaseTarget:
    """Where to connect, and how that target was resolved (for diagnostics)."""

    url: URL
    source: TargetSource

    def redacted(self) -> str:
        return redact_database_url(self.url.render_as_string(hide_password=False))

    def __repr__(self) -> str:
        return f"DatabaseTarget(source={self.source!r}, url={self.redacted()!r})"


def get_binding(scope_env: Any, name: str) -> Any:
    """Read a binding/env var from either the Workers env proxy or a plain mapping.

    The Workers env object exposes bindings as attributes (a JS proxy), not as a
    dict, so ``.get()`` is unavailable there.
    """
    if scope_env is None:
        return None
    if isinstance(scope_env, Mapping):
        return scope_env.get(name)
    try:
        return getattr(scope_env, name)
    except AttributeError:
        return None


def target_from_hyperdrive(binding: Any) -> DatabaseTarget:
    """Build a target from the Cloudflare Hyperdrive binding.

    Python Workers expose host/port/user/password/database on the binding. The
    Worker <-> Hyperdrive hop is not TLS, so sslmode=disable.
    """
    url = URL.create(
        "postgresql+psycopg",
        username=binding.user,
        password=binding.password,
        host=binding.host,
        port=int(binding.port),
        database=binding.database,
        query={"sslmode": "disable"},
    )
    return DatabaseTarget(url=url, source="hyperdrive")


def target_from_settings(settings: Settings) -> DatabaseTarget:
    if not settings.database_url:
        raise ConfigError(
            "No HYPERDRIVE binding and no DATABASE_URL set. "
            "Copy .env.example to .env and set DATABASE_URL for local development."
        )
    return DatabaseTarget(url=make_url(settings.database_url), source="settings")


def resolve_target(scope_env: Any, settings: Settings) -> DatabaseTarget:
    """Prefer the Hyperdrive binding (staging/production); else DATABASE_URL (local)."""
    binding = get_binding(scope_env, HYPERDRIVE_BINDING)
    if binding is not None:
        return target_from_hyperdrive(binding)
    return target_from_settings(settings)


def driver_connect_args(settings: Settings) -> dict[str, Any]:
    """libpq/psycopg connection options (applied per connection)."""
    return {
        "connect_timeout": settings.db_connect_timeout_seconds,
        "application_name": settings.db_application_name,
    }


def create_db_engine(target: DatabaseTarget, settings: Settings) -> Engine:
    """Create a per-request engine. NullPool: no sockets held between requests."""
    return create_engine(
        target.url,
        poolclass=NullPool,
        connect_args=driver_connect_args(settings),
        future=True,
    )


@contextlib.contextmanager
def engine_for(target: DatabaseTarget, settings: Settings) -> Iterator[Engine]:
    engine = create_db_engine(target, settings)
    try:
        yield engine
    finally:
        engine.dispose()


@contextlib.contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transaction boundary: commit on success, rollback on error, always close."""
    session = Session(bind=engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def check_connectivity(engine: Engine) -> tuple[bool, float, str | None]:
    """Run ``SELECT 1``. Returns (ok, elapsed_ms, error_type_or_None).

    Never returns the exception message — it can contain credentials/host details.
    """
    started = time.perf_counter()
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, (time.perf_counter() - started) * 1000.0, None
    except Exception as exc:  # noqa: BLE001 - only the type name is surfaced
        return False, (time.perf_counter() - started) * 1000.0, type(exc).__name__
