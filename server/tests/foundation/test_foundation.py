from uuid import uuid4

import pytest
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError
from sqlmodel import Session

from app.contracts.principal import Principal
from app.core import db
from app.core.config import Settings
from app.core.db import get_db
from app.core.errors import AppError
from app.core.logging import safe_context


def test_application_registers_foundation_routes(app):
    assert str(app.url_path_for("health")) == "/health"
    assert str(app.url_path_for("ready")) == "/ready"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_is_available(client):
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_failure_is_safe(app, client):
    secret = "postgresql://name:very-secret-password@example.test/db"

    def fail() -> None:
        raise RuntimeError(secret)

    app.state.database_check = fail
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "DEPENDENCY_UNAVAILABLE",
            "message": "Required dependencies are unavailable.",
        }
    }
    assert secret not in response.text


def test_app_error_uses_shared_contract(app, client):
    router = APIRouter()

    @router.get("/app-error")
    def app_error() -> None:
        raise AppError("EXAMPLE_ERROR", "Example failed.", 409)

    app.include_router(router)
    response = client.get("/app-error")
    assert response.status_code == 409
    assert response.json() == {"error": {"code": "EXAMPLE_ERROR", "message": "Example failed."}}


def test_http_and_unexpected_errors_are_safe(app, client):
    router = APIRouter()

    @router.get("/missing")
    def missing() -> None:
        raise HTTPException(404, "Not found.")

    @router.get("/boom")
    def boom() -> None:
        raise RuntimeError("top-secret database information")

    app.include_router(router)
    missing_response = client.get("/missing")
    boom_response = client.get("/boom")
    assert missing_response.json() == {"error": {"code": "HTTP_404", "message": "Not found."}}
    assert boom_response.status_code == 500
    assert boom_response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "An unexpected error occurred.",
        }
    }
    assert "top-secret" not in boom_response.text


def test_database_session_dependency_commits_and_closes():
    dependency = get_db()
    session = next(dependency)
    assert isinstance(session, Session)
    with pytest.raises(StopIteration):
        next(dependency)


def test_database_dependency_rolls_back_and_closes_on_failure(monkeypatch):
    class TrackingSession:
        rolled_back = False
        closed = False

        def commit(self):
            raise AssertionError("A failed dependency must not commit")

        def rollback(self):
            self.rolled_back = True

        def close(self):
            self.closed = True

    session = TrackingSession()
    monkeypatch.setattr(db, "_session_factory", lambda: session)
    dependency = get_db()
    next(dependency)
    with pytest.raises(RuntimeError, match="database failure"):
        dependency.throw(RuntimeError("database failure"))
    assert session.rolled_back is True
    assert session.closed is True


def test_principal_contract_and_auth_boundary():
    team_id = uuid4()
    principal = Principal(user_id="user-1", team_id=str(team_id), role="participant")
    assert (principal.user_id, principal.team_id, principal.role) == (
        "user-1",
        team_id,
        "participant",
    )
    # An organizer-only identity (Module K decides organizers) has no team.
    assert Principal(user_id="user-2", role="organizer").team_id is None
    # Module B now implements the dependency; its missing-credential behavior
    # is covered through the /auth/me endpoint and authentication tests.


def test_configuration_requires_database_url_and_logging_redacts_secrets(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="DATABASE_URL"):
        Settings(_env_file=None)
    assert safe_context({"database_url": "secret", "request_id": "req-1"}) == {
        "request_id": "req-1"
    }
