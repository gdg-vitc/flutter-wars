"""Modules D, E, F, K tests: the migrated schema from tests/conftest.py (real PostgreSQL)."""

import uuid
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlmodel import Session

from app.core.auth import Principal, get_principal
from app.core.db import get_engine
from app.main import app


@pytest.fixture(scope="session", autouse=True)
def _schema(migrated_schema) -> None:
    """Shared migrated schema (tests/conftest.py)."""


@pytest.fixture()
def db() -> Iterator[Session]:
    with Session(get_engine()) as s:
        yield s
        s.rollback()


@pytest.fixture()
def make_team() -> Callable[[], uuid.UUID]:
    """Every test gets fresh teams, so tests never need to clean up (ledger is append-only anyway)."""

    def _make() -> uuid.UUID:
        tid = uuid.uuid4()
        with get_engine().begin() as conn:
            conn.execute(text("INSERT INTO team (id, name) VALUES (:id, :n)"), {"id": tid, "n": f"t-{tid.hex[:8]}"})
        return tid

    return _make


@pytest.fixture()
def make_widget() -> Callable[..., str]:
    """Insert a catalog widget directly (Module D's table). Returns its id."""

    def _make(widget_id: str | None = None, status: str = "ACTIVE") -> str:
        wid = widget_id or f"w_{uuid.uuid4().hex[:10]}"
        with get_engine().begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO widget (id, appdev_key, display_name, category, status, archived_at) "
                    "VALUES (:id, :k, :n, 'test', :s, CASE WHEN CAST(:s AS VARCHAR) = 'ARCHIVED' THEN now() END) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": wid, "k": f"appdev.{wid}", "n": wid.title()[:60], "s": status},
            )
        return wid

    return _make


@pytest.fixture()
def client() -> Iterator[TestClient]:
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def login() -> Callable[..., None]:
    """login(team_id=t)  -> a participant of team t (Module B's job in production)
    login(email="x@y")  -> just that Google identity, no team (Module K decides if it's an organizer)"""

    def _login(team_id: uuid.UUID | None = None, email: str | None = None) -> None:
        if email is not None:
            p = Principal(user_id=f"user-{email}", email=email, team_id=team_id)
        else:
            p = Principal(user_id=f"user-{team_id}", email=f"member-{team_id}@student.test", team_id=team_id)
        app.dependency_overrides[get_principal] = lambda: p

    return _login
