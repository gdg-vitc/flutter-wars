import uuid

from sqlalchemy import text

from app.core.db import engine
from app.main import app
from app.modules.admin import Permission, Role
from app.modules.admin.permissions import ROLE_PERMISSIONS


def _admin_routes() -> list[tuple[str, str]]:
    """Every /admin endpoint, read from the OpenAPI schema (works across FastAPI versions)."""
    app.openapi_schema = None
    out = []
    for path, ops in app.openapi()["paths"].items():
        if not path.startswith("/admin"):
            continue
        concrete = (
            path.replace("{team_id}", str(uuid.uuid4()))
            .replace("{organizer_id}", str(uuid.uuid4()))
            .replace("{scope}", "ALL")
            .replace("{widget_id}", "some_widget")
        )
        out += [(m.upper(), concrete) for m in ops]
    return out


def test_every_admin_route_rejects_participants(client, login, make_team):
    routes = _admin_routes()
    assert len(routes) >= 28  # K (14) + E/F (8) + D (6) admin operations; more may be added by other teams
    login(team_id=make_team())
    for method, path in routes:
        r = client.request(method, path, json={})
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["error"]["code"] == "FORBIDDEN"


def test_unknown_email_is_rejected_same_as_participant(client, login):
    login(email="stranger@gmail.test")
    r = client.get("/admin/me")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_email_match_is_case_insensitive(client, login, make_organizer):
    _, email = make_organizer(role="VIEWER")
    login(email=email.upper())
    assert client.get("/admin/me").json()["email"] == email


def test_deactivation_takes_effect_on_next_request(client, login, make_organizer):
    oid, email = make_organizer(role="OPERATOR")
    login(email=email)
    assert client.get("/admin/teams").status_code == 200
    with engine.begin() as c:
        c.execute(text("UPDATE organizer SET active = false WHERE id = :i"), {"i": oid})
    r = client.get("/admin/teams")
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"


def test_viewer_can_read_but_not_change(client, login, login_organizer, make_team):
    t = make_team()
    login_organizer("VIEWER")
    assert client.get("/admin/teams").status_code == 200
    assert client.get(f"/admin/teams/{t}/wallet").status_code == 200
    assert client.get("/admin/audit").status_code == 200
    r = client.post(
        f"/admin/teams/{t}/credits",
        json={"amount": 5, "reason": "viewer try", "idempotency_key": str(uuid.uuid4())},
    )
    assert r.status_code == 403
    assert r.json()["error"] == {
        "code": "MISSING_PERMISSION",
        "message": "Your organizer role cannot do this",
        "details": {"permission": "credits.adjust"},
    }
    assert client.put("/admin/controls/TRADING", json={"frozen": True, "reason": "nope"}).status_code == 403


def test_operator_cannot_manage_organizers(client, login, login_organizer):
    login_organizer("OPERATOR")
    assert client.get("/admin/organizers").status_code == 403
    body = {"email": "x@gdg.test", "display_name": "X", "role": "OWNER", "reason": "self promote"}
    r = client.post("/admin/organizers", json=body)
    assert r.status_code == 403 and r.json()["error"]["details"]["permission"] == "organizers.manage"


def test_me_lists_permissions(client, login, login_organizer):
    email = login_organizer("VIEWER")
    me = client.get("/admin/me").json()
    assert me["email"] == email and me["role"] == "VIEWER"
    assert me["permissions"] == ["audit.read", "view"]


def test_role_table_is_strictly_nested():
    v, o, w = ROLE_PERMISSIONS[Role.VIEWER], ROLE_PERMISSIONS[Role.OPERATOR], ROLE_PERMISSIONS[Role.OWNER]
    assert v < o < w
    assert w == frozenset(Permission)
    assert Permission.ORGANIZERS_MANAGE in w and Permission.ORGANIZERS_MANAGE not in o


def test_no_login_is_rejected(client):
    assert client.get("/admin/me").status_code == 501  # TEMP stub: becomes 401 once Module B lands
