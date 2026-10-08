from fastapi import status
from sqlmodel import SQLModel

from app.contracts.principal import Principal
from app.core.db import get_engine
from app.core.principal import get_principal
from app.modules.ide_sync.contracts import WidgetAllowance
from app.modules.ide_sync.model import TeamApiKey


class FakeInventory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_team_inventory(self, team_id: str) -> list[WidgetAllowance]:
        self.calls.append(team_id)
        return [WidgetAllowance(widget_id=f"button_{team_id}", quantity=3)]


def _organizer() -> Principal:
    return Principal(user_id="organizer-1", team_id="staff", role="organizer")


def _participant() -> Principal:
    return Principal(user_id="person-1", team_id="team-a", role="participant")


def _prepare(app) -> FakeInventory:
    SQLModel.metadata.create_all(get_engine())
    inventory = FakeInventory()
    app.state.inventory_reader = inventory
    app.dependency_overrides[get_principal] = _organizer
    return inventory


def _issue(client, team_id: str = "team-a") -> dict:
    response = client.post(f"/admin/teams/{team_id}/api-keys")
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


def test_organizer_issues_once_only_key_and_ide_state_is_team_scoped(app, client):
    inventory = _prepare(app)
    issued = _issue(client)
    assert issued["api_key"].startswith("twk_")

    with get_engine().connect() as connection:
        stored_hash = connection.execute(
            TeamApiKey.__table__.select().where(TeamApiKey.key_id == issued["key_id"])
        ).mappings().one()["secret_hash"]
    assert issued["api_key"] not in stored_hash

    response = client.get(
        "/ide/state?team_id=team-b", headers={"X-Team-API-Key": issued["api_key"]}
    )
    assert response.status_code == 200
    assert response.json() == {
        "team_id": "team-a",
        "widgets": [{"widget_id": "button_team-a", "quantity": 3}],
    }
    assert inventory.calls == ["team-a"]
    assert "credit" not in response.text


def test_revoked_malformed_unknown_and_missing_keys_fail_safely(app, client):
    _prepare(app)
    issued = _issue(client)
    active = client.get("/ide/state", headers={"X-Team-API-Key": issued["api_key"]})
    assert active.status_code == 200
    revoked = client.delete(f"/admin/teams/team-a/api-keys/{issued['key_id']}")
    assert revoked.status_code == 200
    for key in (issued["api_key"], "not-a-key", "twk_unknown_secret", None):
        headers = {} if key is None else {"X-Team-API-Key": key}
        response = client.get("/ide/state", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"code": "API_KEY_INVALID", "message": "API key is invalid."}


def test_participant_cannot_manage_keys(app, client):
    _prepare(app)
    app.dependency_overrides[get_principal] = _participant
    response = client.post("/admin/teams/team-a/api-keys")
    assert response.status_code == 403
    assert response.json() == {"code": "FORBIDDEN", "message": "Organizer access is required."}


def test_unconfigured_inventory_is_a_safe_dependency_failure(app, client):
    SQLModel.metadata.create_all(get_engine())
    app.dependency_overrides[get_principal] = _organizer
    issued = _issue(client)
    response = client.get("/ide/state", headers={"X-Team-API-Key": issued["api_key"]})
    assert response.status_code == 503
    assert response.json() == {
        "code": "DEPENDENCY_UNAVAILABLE",
        "message": "Required dependencies are unavailable.",
    }
