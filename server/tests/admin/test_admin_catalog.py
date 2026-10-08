"""Organizer routes for the widget catalog (Module D data, served by Module K)."""

import uuid


def _body(**kw):
    wid = kw.pop("id", f"w_{uuid.uuid4().hex[:10]}")
    return {"id": wid, "appdev_key": f"flutter_{wid}", "display_name": "Icon Pack", "category": "media", **kw}


def _audit(client, wid):
    return [a["action"] for a in client.get(f"/admin/audit?target_type=widget&target_id={wid}").json()["items"]]


def test_create_widget_route(client, login_organizer):  # (teammate)
    login_organizer()
    body = _body(flutter_classes=["Icon", "IconButton", "Icon"])
    r = client.post("/admin/widgets", json=body)
    assert r.status_code == 201
    d = r.json()
    assert (d["id"], d["status"], d["version"], d["archived"]) == (body["id"], "ACTIVE", 1, False)
    assert d["flutter_classes"] == ["Icon", "IconButton"]  # de-duplicated
    assert _audit(client, body["id"]) == ["catalog.widget_create"]


def test_duplicates_rejected(client, login_organizer):
    login_organizer()
    b = _body()
    assert client.post("/admin/widgets", json=b).status_code == 201
    r = client.post("/admin/widgets", json=b)
    assert r.status_code == 409 and r.json()["error"]["details"] == {"field": "id"}
    r = client.post("/admin/widgets", json=_body(appdev_key=b["appdev_key"]))
    assert r.status_code == 409 and r.json()["error"]["details"] == {"field": "appdev_key"}


def test_update_widget_version_conflict_route(client, login_organizer):  # (teammate)
    login_organizer()
    b = _body()
    client.post("/admin/widgets", json=b)
    r = client.patch(f"/admin/widgets/{b['id']}", json={"display_name": "Updated", "expected_version": 99})
    assert r.status_code == 409 and r.json()["error"]["code"] == "VERSION_CONFLICT"
    r = client.patch(f"/admin/widgets/{b['id']}", json={"display_name": "Updated", "expected_version": 1})
    assert r.status_code == 200 and r.json()["version"] == 2
    assert _audit(client, b["id"]) == ["catalog.widget_update", "catalog.widget_create"]


def test_identity_fields_are_immutable(client, login_organizer):
    login_organizer()
    b = _body()
    client.post("/admin/widgets", json=b)
    for field in ("id", "appdev_key"):
        r = client.patch(f"/admin/widgets/{b['id']}", json={field: "changed", "expected_version": 1})
        assert r.status_code == 422 and r.json()["error"] == {
            "code": "FIELD_IMMUTABLE",
            "message": f"'{field}' can never change once a widget exists",
            "details": {"field": field},
        }


def test_archive_needs_typed_id_and_can_be_restored(client, login_organizer):  # (teammate, extended)
    login_organizer()
    b = _body()
    client.post("/admin/widgets", json=b)
    url = f"/admin/widgets/{b['id']}"
    r = client.post(f"{url}/archive", json={"expected_version": 1, "reason": "replaced", "confirm": "wrong"})
    assert r.status_code == 422 and r.json()["error"]["details"] == {"expected": b["id"]}
    r = client.post(f"{url}/archive", json={"expected_version": 1, "reason": "replaced", "confirm": b["id"]})
    assert r.status_code == 200 and r.json()["status"] == "ARCHIVED" and r.json()["archived_at"]
    r = client.post(f"{url}/restore", json={"expected_version": 2, "reason": "archived by mistake"})
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE" and r.json()["archived_at"] is None
    assert _audit(client, b["id"]) == ["catalog.widget_restore", "catalog.widget_archive", "catalog.widget_create"]


def test_organizer_list_includes_archived_and_notes(client, login_organizer):
    login_organizer()
    b = _body(internal_notes="give away in round 3")
    client.post("/admin/widgets", json=b)
    client.post(f"/admin/widgets/{b['id']}/archive", json={"expected_version": 1, "reason": "done", "confirm": b["id"]})
    rows = {w["id"]: w for w in client.get("/admin/widgets").json()}
    assert rows[b["id"]]["archived"] is True and rows[b["id"]]["internal_notes"] == "give away in round 3"
    assert b["id"] not in {w["id"] for w in client.get("/admin/widgets?include_archived=false").json()}
    assert client.get(f"/admin/widgets/{b['id']}").json()["version"] == 2


def test_create_validation(client, login_organizer):
    login_organizer()
    for bad in (
        _body(id="Bad-ID"),
        _body(id="x"),
        _body(display_name="<script>alert(1)</script>"),
        _body(display_name="x" * 61),
        _body(category="Media Stuff"),
        _body(flutter_classes=["not a class"]),
        _body(flutter_classes=[f"C{i}" for i in range(21)]),
        _body(is_free=True),  # price/stock fields don't exist in the catalog (spec §6)
        _body(price=5),
        _body(stock=10),
        _body(appdev_key="has space"),
        _body(version=5),  # extra field
    ):
        r = client.post("/admin/widgets", json=bad)
        assert r.status_code == 422, bad
        assert r.json()["error"]["code"] == "VALIDATION_ERROR"
        errs = r.json()["error"]["details"]["errors"]
        assert all(set(e) == {"loc", "msg", "type"} for e in errs)  # submitted values are never echoed back


def test_patch_requires_version_and_rejects_nulls(client, login_organizer):
    login_organizer()
    b = _body()
    client.post("/admin/widgets", json=b)
    url = f"/admin/widgets/{b['id']}"
    assert client.patch(url, json={"display_name": "x"}).status_code == 422  # no expected_version
    r = client.patch(url, json={"display_name": None, "expected_version": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_WIDGET_DATA"


def test_permissions(client, login, login_organizer, make_team):
    b = _body()
    login_organizer("VIEWER")
    assert client.get("/admin/widgets").status_code == 200
    r = client.post("/admin/widgets", json=b)
    assert r.status_code == 403 and r.json()["error"]["details"] == {"permission": "catalog.manage"}
    login(team_id=make_team())
    assert client.post("/admin/widgets", json=b).status_code == 403
    assert client.get("/admin/widgets").status_code == 403


def test_unknown_widget_admin_routes(client, login_organizer):
    login_organizer()
    assert client.get("/admin/widgets/nope_nope").status_code == 404
    r = client.patch("/admin/widgets/nope_nope", json={"display_name": "x", "expected_version": 1})
    assert r.status_code == 404
