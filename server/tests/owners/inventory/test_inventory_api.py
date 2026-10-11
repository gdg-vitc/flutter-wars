from sqlalchemy import text
from sqlmodel import Session

from app.core.db import get_engine
from app.modules.inventory import service as inventory


def _give(team, widget, qty, ref):
    with Session(get_engine()) as s:
        inventory.increment(s, team, widget, qty, ref_type="purchase", ref_id=ref, reason="r", actor="t")
        s.commit()


def test_participant_sees_only_own_inventory(client, login, make_team, make_widget):
    a, b, w = make_team(), make_team(), make_widget()
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE widget SET description=:d WHERE id=:w"), {"d": "Horizontal layout.", "w": w})
    _give(a, w, 2, "x1")
    _give(b, w, 7, "x2")
    login(team_id=a)
    body = client.get("/inventory").json()
    assert body["team_id"] == str(a)
    assert body["items"][0]["description"] == "Horizontal layout."
    assert [(i["widget_id"], i["quantity"]) for i in body["items"]] == [(w, 2)]
    assert set(body["items"][0]) == {"widget_id", "appdev_key", "display_name", "description", "quantity", "archived"}


def test_empty_inventory_is_an_empty_list(client, login, make_team):
    t = make_team()
    login(team_id=t)
    assert client.get("/inventory").json() == {"team_id": str(t), "items": []}


def test_inventory_needs_a_team_login(client, login):
    login(email="someone@gmail.test")
    assert client.get("/inventory").status_code == 403
