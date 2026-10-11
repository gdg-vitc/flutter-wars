"""Participant catalog routes. Organizer routes are tested in tests/admin/test_admin_catalog.py."""

import uuid

from sqlmodel import Session

from app.core.db import get_engine
from app.modules.catalog import service as catalog


def _widget(**kw):
    wid = f"w_{uuid.uuid4().hex[:10]}"
    with Session(get_engine()) as s:
        catalog.create_widget(
            s,
            widget_id=wid,
            appdev_key=f"k.{wid}",
            display_name="Row Layout",
            category="layout",
            internal_notes="secret organizer note",
            **kw,
        )
        s.commit()
    return wid


def test_public_list_hides_internal_fields(client, login, make_team):  # (teammate, adapted)
    wid = _widget(flutter_classes=["Row"])
    login(team_id=make_team())
    r = client.get("/widgets")
    assert r.status_code == 200
    item = next(x for x in r.json() if x["id"] == wid)
    assert set(item) == {
        "id",
        "appdev_key",
        "display_name",
        "description",
        "category",
        "flutter_classes",
        "archived",
    }
    assert "internal_notes" not in item and "status" not in item and "version" not in item


def test_archived_widget_hidden_from_list_but_resolvable(client, login, make_team):  # (teammate, changed)
    wid = _widget()
    with Session(get_engine()) as s:
        catalog.archive_widget(s, wid, expected_version=1)
        s.commit()
    login(team_id=make_team())
    assert wid not in {x["id"] for x in client.get("/widgets").json()}
    r = client.get(f"/widgets/{wid}")  # a team that owns it can still see what it is
    assert r.status_code == 200 and r.json()["archived"] is True


def test_unknown_and_malformed_ids(client, login, make_team):
    login(team_id=make_team())
    r = client.get("/widgets/does_not_exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "WIDGET_NOT_FOUND"
    r = client.get("/widgets/Bad-Id!")
    assert r.status_code == 422 and r.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"


def test_catalog_needs_login(client):
    assert client.get("/widgets").status_code == 401  # TEMP stub: 401 once Module B lands


def test_organizer_without_team_can_read_catalog(client, login):
    login(email="someone@gdg.test")
    assert client.get("/widgets").status_code == 200
