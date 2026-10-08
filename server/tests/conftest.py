"""Test setup: real PostgreSQL only (row locks, CHECKs and triggers must be real).

Run:  pytest   (uses TEST_DATABASE_URL, default postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_test)
The test database is wiped (schema dropped) at the start of every run. NEVER point this at Neon prod.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

# Tests ALWAYS use TEST_DATABASE_URL (never the dev/prod DATABASE_URL) and refuse non-test databases.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:pw@localhost:5432/flutterwars_test"
)
assert "test" in os.environ["DATABASE_URL"], "Refusing to run tests against a non-test database"

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlmodel import Session  # noqa: E402

from app.core.auth import Principal, get_principal  # noqa: E402
from app.core.db import engine  # noqa: E402
from app.main import app  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    command.upgrade(cfg, "head")
    yield


@pytest.fixture()
def db() -> Iterator[Session]:
    with Session(engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def make_team() -> Callable[[], uuid.UUID]:
    """Every test gets fresh teams, so tests never need to clean up (ledger is append-only anyway)."""

    def _make() -> uuid.UUID:
        tid = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO team (id, name) VALUES (:id, :n)"), {"id": tid, "n": f"t-{tid.hex[:8]}"})
        return tid

    return _make


@pytest.fixture()
def make_widget() -> Callable[..., str]:
    """Insert a catalog widget directly (Module D's table). Returns its id."""

    def _make(widget_id: str | None = None, status: str = "ACTIVE") -> str:
        wid = widget_id or f"w_{uuid.uuid4().hex[:10]}"
        with engine.begin() as conn:
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
