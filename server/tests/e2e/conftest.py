"""End-to-end: the real app, real JWTs, real owner modules, the migrated schema.

No dependency overrides: requests authenticate with Module B tokens, organizers are
Module K rows, and trading/auction run on the real Catalog, Ledger and Inventory.
"""

from collections.abc import Callable, Iterator
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.db import get_engine
from app.main import create_app
from app.modules.admin import service as admin
from app.modules.authentication.jwt import create_access_token
from app.modules.authentication.model import UserIdentity


@pytest.fixture(scope="session", autouse=True)
def _schema(migrated_schema) -> None:
    """Shared migrated schema (tests/conftest.py)."""


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def _token(app, user: UserIdentity, team_id: UUID | None, role: str) -> dict[str, str]:
    token = create_access_token(
        user_id=str(user.id),
        email=user.email,
        team_id=str(team_id) if team_id else None,
        role=role,
        settings=app.state.settings,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def organizer(app) -> dict[str, str]:
    """A Module K OWNER, logged in through Module B (organizer-only token)."""
    email = f"owner-{uuid4().hex[:8]}@gdg.test"
    with Session(get_engine()) as s:
        admin.bootstrap_owner(s, email=email, display_name="Event Lead")
        user = UserIdentity(google_subject=f"google-{uuid4().hex}", email=email)
        s.add(user)
        s.commit()
        s.refresh(user)
    return _token(app, user, None, "organizer")


@pytest.fixture()
def team_login(app) -> Callable[[UUID, str], dict[str, str]]:
    """Headers for a member Module B registered when the organizer created the team."""

    def _login(team_id: UUID, email: str) -> dict[str, str]:
        with Session(get_engine()) as s:
            user = s.exec(select(UserIdentity).where(UserIdentity.email == email)).one()
            user.google_subject = user.google_subject or f"google-{uuid4().hex}"
            s.add(user)
            s.commit()
            s.refresh(user)
        return _token(app, user, team_id, "participant")

    return _login
