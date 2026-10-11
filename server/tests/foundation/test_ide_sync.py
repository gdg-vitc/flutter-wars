from uuid import uuid4

from fastapi import status
from sqlmodel import Session

from app.contracts.inventory import InventoryGateway
from app.contracts.inventory import InventoryItem as WidgetAllowance
from app.contracts.principal import Principal
from app.core.auth import require_organizer
from app.core.db import get_engine
from app.core.principal import get_principal
from app.core.services import override, provide
from app.modules.admin import OrganizerPrincipal, Role
from app.modules.admin.permissions import ROLE_PERMISSIONS
from app.modules.authentication.model import Team
from app.modules.ide_sync.model import TeamApiKey


class FakeInventory:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_team_inventory(self, team_id: str) -> list[WidgetAllowance]:
        team_id = str(team_id)
        self.calls.append(team_id)
        return [
            WidgetAllowance(
                widget_id=f"button_{team_id}", quantity=3, appdev_key="button", display_name="Button", archived=False
            )
        ]


def _team() -> str:
    """Keys can only be issued for a team Module B knows."""
    with Session(get_engine()) as session:
        team = Team(name=f"Team {uuid4().hex}")
        session.add(team)
        session.commit()
        return str(team.id)


def _organizer() -> OrganizerPrincipal:
    """Module K decides organizers; this stands in for an OPERATOR row in its table."""
    return OrganizerPrincipal(
        id=uuid4(),
        email="organizer@example.test",
        display_name="Organizer",
        role=Role.OPERATOR,
        permissions=ROLE_PERMISSIONS[Role.OPERATOR],
    )


def _participant() -> Principal:
    return Principal(user_id="person-1", team_id=uuid4(), role="participant", email="p@example.test")


def _prepare(app) -> FakeInventory:
    inventory = FakeInventory()
    provide(InventoryGateway, lambda session: inventory)
    app.dependency_overrides[require_organizer] = _organizer
    return inventory


def _issue(client, team_id: str) -> dict:
    response = client.post(f"/admin/teams/{team_id}/api-keys")
    assert response.status_code == status.HTTP_201_CREATED
    return response.json()


def test_organizer_issues_once_only_key_and_ide_state_is_team_scoped(app, client):
    inventory = _prepare(app)
    team_a, team_b = _team(), _team()
    issued = _issue(client, team_a)
    assert issued["api_key"].startswith("twk_")

    with get_engine().connect() as connection:
        stored_hash = (
            connection.execute(TeamApiKey.__table__.select().where(TeamApiKey.key_id == issued["key_id"]))
            .mappings()
            .one()["secret_hash"]
        )
    assert issued["api_key"] not in stored_hash

    response = client.get(f"/ide/state?team_id={team_b}", headers={"X-Team-API-Key": issued["api_key"]})
    assert response.status_code == 200
    assert response.json() == {
        "team_id": team_a,
        "widgets": [{"widget_id": f"button_{team_a}", "quantity": 3}],
    }
    assert inventory.calls == [team_a]
    assert "credit" not in response.text


def test_revoked_malformed_unknown_and_missing_keys_fail_safely(app, client):
    _prepare(app)
    team = _team()
    issued = _issue(client, team)
    active = client.get("/ide/state", headers={"X-Team-API-Key": issued["api_key"]})
    assert active.status_code == 200
    revoked = client.delete(f"/admin/teams/{team}/api-keys/{issued['key_id']}")
    assert revoked.status_code == 200
    for key in (issued["api_key"], "not-a-key", "twk_unknown_secret", None):
        headers = {} if key is None else {"X-Team-API-Key": key}
        response = client.get("/ide/state", headers=headers)
        assert response.status_code == 401
        assert response.json() == {"error": {"code": "API_KEY_INVALID", "message": "API key is invalid."}}


def test_participant_cannot_manage_keys(app, client):
    """A team member is not in Module K's organizer table."""
    app.dependency_overrides[get_principal] = _participant
    response = client.post(f"/admin/teams/{_team()}/api-keys")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_unconfigured_inventory_is_a_safe_dependency_failure(app, client):
    _prepare(app)
    issued = _issue(client, _team())
    with override(InventoryGateway, None):
        response = client.get("/ide/state", headers={"X-Team-API-Key": issued["api_key"]})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_NOT_AVAILABLE"
