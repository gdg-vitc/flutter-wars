"""Organizer routes for credits (Module E) and widgets (Module F). They live in Module K."""

import uuid


def test_initial_grant_and_adjust(client, login_organizer, make_team):
    t = make_team()
    org_email = login_organizer()
    r = client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 120})
    assert r.status_code == 201 and r.json()["balance_after"] == 120
    assert client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 120}).status_code == 409

    key = str(uuid.uuid4())
    body = {"amount": 10, "reason": "won challenge", "idempotency_key": key}
    r = client.post(f"/admin/teams/{t}/credits", json=body)
    assert r.status_code == 201 and r.json()["actor"] == org_email
    assert client.post(f"/admin/teams/{t}/credits", json=body).status_code == 409  # double click
    assert client.get(f"/admin/teams/{t}/wallet").json()["balance"] == 130
    assert client.get(f"/admin/teams/{t}/wallet/verify").json()["ok"] is True
    entries = client.get(f"/admin/teams/{t}/wallet/ledger").json()
    assert [e["kind"] for e in entries] == ["ADJUST", "GRANT"] and entries[0]["actor"] == org_email


def test_credit_body_validation(client, login_organizer, make_team):
    t = make_team()
    login_organizer()
    k = str(uuid.uuid4())
    for bad in (
        {"amount": 10.5, "reason": "x reason", "idempotency_key": k},  # float
        {"amount": "10", "reason": "x reason", "idempotency_key": k},  # string
        {"amount": 0, "reason": "x reason", "idempotency_key": k},  # zero
        {"amount": 10, "reason": "  ", "idempotency_key": k},  # blank reason
        {"amount": 10, "reason": "ok reason"},  # no key
        {"amount": 10, "reason": "ok reason", "idempotency_key": k, "team_id": str(t)},  # extra field
    ):
        assert client.post(f"/admin/teams/{t}/credits", json=bad).status_code == 422, bad


def test_unknown_team_grant_is_404(client, login_organizer):
    login_organizer()
    r = client.post(f"/admin/teams/{uuid.uuid4()}/credits/initial-grant", json={"amount": 10})
    assert r.status_code == 404 and r.json()["error"]["code"] == "TEAM_NOT_FOUND"


def test_inventory_adjust_and_verify(client, login_organizer, make_team, make_widget):
    t, w = make_team(), make_widget()
    org_email = login_organizer()
    key = str(uuid.uuid4())
    body = {"widget_id": w, "delta": 2, "reason": "lost purchase fix", "idempotency_key": key}
    r = client.post(f"/admin/teams/{t}/inventory/adjust", json=body)
    assert r.status_code == 201 and r.json()["actor"] == org_email
    assert client.post(f"/admin/teams/{t}/inventory/adjust", json=body).status_code == 409
    too_many = {"widget_id": w, "delta": -3, "reason": "remove", "idempotency_key": str(uuid.uuid4())}
    r = client.post(f"/admin/teams/{t}/inventory/adjust", json=too_many)
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_QUANTITY"
    assert client.get(f"/admin/teams/{t}/inventory/verify").json() == {"team_id": str(t), "ok": True, "mismatches": []}
    items = client.get(f"/admin/teams/{t}/inventory").json()["items"]
    assert [(i["widget_id"], i["quantity"]) for i in items] == [(w, 2)]


def test_inventory_adjust_validation(client, login_organizer, make_team, make_widget):
    t, w = make_team(), make_widget()
    login_organizer()
    k = str(uuid.uuid4())
    for bad in (
        {"widget_id": w, "delta": 0, "reason": "zero", "idempotency_key": k},
        {"widget_id": w, "delta": 1.0, "reason": "float", "idempotency_key": k},
        {"widget_id": "Bad-ID!", "delta": 1, "reason": "bad id", "idempotency_key": k},
        {"widget_id": w, "delta": 1, "reason": "ok reason", "idempotency_key": k, "quantity": 99},
    ):
        assert client.post(f"/admin/teams/{t}/inventory/adjust", json=bad).status_code == 422, bad


def test_viewer_cannot_change_credits_or_widgets(client, login_organizer, make_team, make_widget):
    t, w = make_team(), make_widget()
    login_organizer("VIEWER")
    key = str(uuid.uuid4())
    r1 = client.post(f"/admin/teams/{t}/credits", json={"amount": 5, "reason": "try it", "idempotency_key": key})
    r2 = client.post(
        f"/admin/teams/{t}/inventory/adjust",
        json={"widget_id": w, "delta": 1, "reason": "try it", "idempotency_key": key},
    )
    assert (r1.status_code, r2.status_code) == (403, 403)
    assert r2.json()["error"]["context"] == {"permission": "inventory.adjust"}
