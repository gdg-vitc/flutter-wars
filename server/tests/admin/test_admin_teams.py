import uuid

from sqlalchemy import text
from sqlmodel import Session

from app.core.db import engine
from app.modules import inventory
from app.modules.admin import ports


def _name(prefix="Team"):
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def _team_count(names):
    with engine.connect() as c:
        return c.execute(text("SELECT count(*) FROM team WHERE name = ANY(:n)"), {"n": names}).scalar_one()


def test_create_team_grants_starting_credits(client, login, login_organizer):
    login_organizer()
    name = _name()
    body = {"name": f"  {name} ", "member_emails": ["A@vit.test", "a@vit.test", "b@vit.test"], "reason": "signup"}
    r = client.post("/admin/teams", json=body)
    assert r.status_code == 201
    t = r.json()
    assert t["name"] == name and t["status"] == "ACTIVE"
    assert (t["balance"], t["held"], t["available"], t["units_owned"]) == (120, 0, 120, 0)
    ledger_rows = client.get(f"/admin/teams/{t['id']}/wallet/ledger").json()  # credits went through Module E
    assert [(e["kind"], e["amount"], e["ref_type"]) for e in ledger_rows] == [("GRANT", 120, "initial_grant")]

    audit = client.get(f"/admin/audit?target_id={t['id']}").json()["items"]
    actions = sorted(a["action"] for a in audit)
    assert actions == ["team.create"]  # the grant's ledger row is the credit record
    assert audit[0]["details"] == {"name": name, "member_count": 2, "initial_credits": 120}
    assert "a@vit.test" not in str(audit)  # member emails are not copied into the audit log


def test_create_team_with_zero_credits(client, login, login_organizer):
    login_organizer()
    t = client.post("/admin/teams", json={"name": _name(), "initial_credits": 0}).json()
    assert t["balance"] == 0


def test_duplicate_team_name_is_409(client, login, login_organizer):
    login_organizer()
    name = _name()
    assert client.post("/admin/teams", json={"name": name}).status_code == 201
    r = client.post("/admin/teams", json={"name": name})
    assert r.status_code == 409 and r.json()["error"]["code"] == "TEAM_NAME_TAKEN"


def test_team_body_validation(client, login, login_organizer):
    login_organizer()
    for bad in (
        {"name": "<script>alert(1)</script>"},
        {"name": "x"},
        {"name": "Fine Name", "initial_credits": 120.0},
        {"name": "Fine Name", "initial_credits": -1},
        {"name": "Fine Name", "member_emails": ["nope"]},
        {"name": "Fine Name", "member_emails": [f"m{i}@x.test" for i in range(11)]},
        {"name": "Fine Name", "id": str(uuid.uuid4())},
    ):
        assert client.post("/admin/teams", json=bad).status_code == 422, bad


def test_import_is_all_or_nothing(client, login, login_organizer):
    login_organizer()
    existing = _name("Taken")
    client.post("/admin/teams", json={"name": existing})
    new = [_name("Imp"), _name("Imp")]
    r = client.post("/admin/teams/import", json={"teams": [{"name": new[0]}, {"name": existing}, {"name": new[1]}]})
    assert r.status_code == 409 and r.json()["error"]["details"]["names"] == [existing]
    assert _team_count(new) == 0  # nothing from the batch was saved


def test_import_rejects_repeats_inside_the_file(client, login, login_organizer):
    login_organizer()
    n = _name("Dup")
    rows = [
        {"name": n, "member_emails": ["x@vit.test"]},
        {"name": n.upper(), "member_emails": ["x@vit.test"]},
    ]
    r = client.post("/admin/teams/import", json={"teams": rows})
    assert r.status_code == 422
    problems = r.json()["error"]["details"]["problems"]
    assert len(problems) == 2 and "repeats row 1" in problems[0]
    assert _team_count([n, n.upper()]) == 0


def test_import_creates_all_with_credits(client, login, login_organizer):
    login_organizer()
    names = [_name("Bulk") for _ in range(3)]
    r = client.post("/admin/teams/import", json={"teams": [{"name": n} for n in names], "initial_credits": 100})
    assert r.status_code == 201
    assert sorted(t["name"] for t in r.json()) == sorted(names)
    assert all(t["balance"] == 100 for t in r.json())


def test_disable_needs_team_name_typed(client, login, login_organizer):
    login_organizer()
    t = client.post("/admin/teams", json={"name": _name()}).json()
    url = f"/admin/teams/{t['id']}/status"
    r = client.post(url, json={"status": "DISABLED", "reason": "left event"})
    assert r.status_code == 422 and r.json()["error"]["details"] == {"expected": t["name"]}
    r = client.post(url, json={"status": "DISABLED", "reason": "left event", "confirm": t["name"]})
    assert r.status_code == 200 and r.json()["status"] == "DISABLED"
    assert client.post(url, json={"status": "ACTIVE", "reason": "came back"}).json()["status"] == "ACTIVE"
    actions = [a["action"] for a in client.get(f"/admin/audit?target_id={t['id']}").json()["items"]]
    assert actions == ["team.status", "team.status", "team.create"]


def test_status_unknown_team_is_404(client, login, login_organizer):
    login_organizer()
    r = client.post(f"/admin/teams/{uuid.uuid4()}/status", json={"status": "ACTIVE", "reason": "abc"})
    assert r.status_code == 404


def test_team_detail_shows_wallet_and_inventory(client, login, login_organizer, make_widget):
    login_organizer()
    t = client.post("/admin/teams", json={"name": _name()}).json()
    w = make_widget()
    with Session(engine) as s:
        inventory.increment(s, uuid.UUID(t["id"]), w, 2, ref_type="purchase", ref_id="p1", reason="buy", actor="t")
        s.commit()
    d = client.get(f"/admin/teams/{t['id']}").json()
    assert d["wallet"] == {"balance": 120, "held": 0, "available": 120}
    assert [(i["widget_id"], i["quantity"]) for i in d["inventory"]] == [(w, 2)]
    overview = {x["id"]: x for x in client.get("/admin/teams").json()}
    assert overview[t["id"]]["units_owned"] == 2
    assert client.get(f"/admin/teams/{uuid.uuid4()}").status_code == 404


def test_overview_query_count_does_not_grow_with_teams(client, login, login_organizer, count_queries):
    """No N+1: 3 queries + 1 organizer lookup, whether there are 5 or 25 more teams."""
    login_organizer()
    for _ in range(5):
        client.post("/admin/teams", json={"name": _name("Q")})
    with count_queries() as q1:
        assert client.get("/admin/teams").status_code == 200
    for _ in range(20):
        client.post("/admin/teams", json={"name": _name("Q")})
    with count_queries() as q2:
        assert client.get("/admin/teams").status_code == 200
    assert len(q1) == len(q2) <= 4


def test_without_team_directory_answers_503(client, login, login_organizer):
    login_organizer()
    saved = ports.team_directory()
    ports.set_team_directory(None)
    try:
        r = client.get("/admin/teams")
        assert r.status_code == 503 and r.json()["error"]["code"] == "DEPENDENCY_NOT_AVAILABLE"
    finally:
        ports.set_team_directory(saved)
