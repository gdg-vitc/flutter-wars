"""Test fixtures.

Runs against in-memory SQLite by default. Set TEST_DATABASE_URL to a
PostgreSQL URL (e.g. a throwaway Neon branch or local container) to run the
same suite against PostgreSQL.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  (registers every table)
from app.core.auth import Principal, Role, get_principal
from app.core.clock import get_now
from app.core.db import get_session
from app.main import create_app
from app.modules.catalog.models import Widget

PG_URL = os.environ.get("TEST_DATABASE_URL")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def _make_engine() -> Engine:
    if PG_URL:
        return create_engine(PG_URL)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = _make_engine()
    tables = [model.__table__ for model in app.models.SQLITE_SAFE]
    SQLModel.metadata.drop_all(engine, tables=tables)
    SQLModel.metadata.create_all(engine, tables=tables)
    yield engine
    SQLModel.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def advance(self, **kwargs: float) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


ORGANIZER = Principal(subject="organizer@example.com", role=Role.ORGANIZER)
PARTICIPANT = Principal(
    subject="player@example.com",
    role=Role.PARTICIPANT,
    team_id=UUID("00000000-0000-4000-8000-0000000000aa"),
)


class Api:
    """TestClient wrapper with a switchable principal."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.principal: Principal | None = ORGANIZER

    def as_(self, principal: Principal | None) -> "Api":
        self.principal = principal
        return self

    def __getattr__(self, name: str):  # get/post/patch/delete
        return getattr(self.client, name)


@pytest.fixture
def api(engine: Engine, clock: Clock) -> Iterator[Api]:
    app = create_app()

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    wrapper: Api

    def _principal() -> Principal:
        if wrapper.principal is None:
            return get_principal()  # the placeholder: always 401
        return wrapper.principal

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_now] = lambda: clock.now
    app.dependency_overrides[get_principal] = _principal
    with TestClient(app) as client:
        wrapper = Api(client)
        yield wrapper


@pytest.fixture
def widgets(session: Session) -> list[Widget]:
    items = [Widget(name=name) for name in ("Button", "ListView", "Card", "AnimatedContainer")]
    items.append(Widget(name="OldWidget", archived=True))
    session.add_all(items)
    session.commit()
    for item in items:
        session.refresh(item)
    return items


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Module M infrastructure tests: the same disposable PostgreSQL database."""
    if not PG_URL:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL infrastructure tests.")
    return PG_URL
