import uuid

import pytest
from sqlalchemy import text

from app.core.db import get_engine
from app.modules.admin.service import _redact, audit


def _audit_for(client, team_id):
    return client.get(f"/admin/audit?target_type=team&target_id={team_id}").json()["items"]


def test_credit_adjust_writes_audit_row(client, login, login_organizer, make_team):
    t = make_team()
    email = login_organizer()
    client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 120})
    key = str(uuid.uuid4())
    client.post(f"/admin/teams/{t}/credits", json={"amount": 15, "reason": "won quiz", "idempotency_key": key})
    rows = _audit_for(client, t)
    assert [r["action"] for r in rows] == ["credits.adjust", "credits.grant_initial"]  # newest first
    adj = rows[0]
    assert adj["actor_email"] == email and adj["actor_role"] == "OPERATOR" and adj["reason"] == "won quiz"
    assert adj["details"]["amount"] == 15 and adj["details"]["balance_after"] == 135
    assert adj["details"]["idempotency_key"] == key  # NOT redacted: it's not a secret


def test_inventory_adjust_writes_audit_row(client, login, login_organizer, make_team, make_widget):
    t, w = make_team(), make_widget()
    login_organizer()
    body = {"widget_id": w, "delta": 3, "reason": "missed purchase", "idempotency_key": str(uuid.uuid4())}
    assert client.post(f"/admin/teams/{t}/inventory/adjust", json=body).status_code == 201
    row = _audit_for(client, t)[0]
    assert row["action"] == "inventory.adjust"
    assert row["details"]["widget_id"] == w and row["details"]["quantity_after"] == 3


def test_failed_action_leaves_no_audit_row(client, login, login_organizer, make_team):
    """Change + audit row are one transaction: if the change fails, there is no audit row."""
    t = make_team()
    login_organizer()
    client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 10})
    body = {"amount": -50, "reason": "penalty", "idempotency_key": str(uuid.uuid4())}
    r = client.post(f"/admin/teams/{t}/credits", json=body)  # only 10 credits -> 409
    assert r.status_code == 409 and r.json()["error"]["code"] == "INSUFFICIENT_CREDITS"
    assert [a["action"] for a in _audit_for(client, t)] == ["credits.grant_initial"]


def test_double_click_writes_one_audit_row(client, login, login_organizer, make_team):
    t = make_team()
    login_organizer()
    client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 50})
    body = {"amount": 5, "reason": "bonus", "idempotency_key": str(uuid.uuid4())}
    client.post(f"/admin/teams/{t}/credits", json=body)
    client.post(f"/admin/teams/{t}/credits", json=body)
    assert [r["action"] for r in _audit_for(client, t)].count("credits.adjust") == 1


@pytest.mark.parametrize(
    "sql",
    ["UPDATE admin_action_log SET reason = 'edited'", "DELETE FROM admin_action_log", "TRUNCATE admin_action_log"],
)
def test_audit_log_is_append_only(login_organizer, sql, client, login, make_team):
    t = make_team()
    login_organizer()
    client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 1})
    with pytest.raises(Exception, match="append-only"), get_engine().begin() as c:
        c.execute(text(sql))


def test_control_rows_cannot_be_deleted():
    with pytest.raises(Exception, match="append-only"), get_engine().begin() as c:
        c.execute(text("DELETE FROM operational_control WHERE scope = 'TRADING'"))


def test_redaction_and_size_limits():
    out = _redact(
        {
            "api_key": "fw_live_123",
            "nested": {"Password": "p", "refresh_token": "r", "ok": 1},
            "idempotency_key": "keep-me",
            "long": "x" * 2000,
            "items": list(range(500)),
            "deep": {"a": {"b": {"c": {"d": {"e": 1}}}}},
            "uid": uuid.UUID(int=1),
        }
    )
    assert out["api_key"] == "[REDACTED]"
    assert out["nested"] == {"Password": "[REDACTED]", "refresh_token": "[REDACTED]", "ok": 1}
    assert out["idempotency_key"] == "keep-me"
    assert len(out["long"]) == 500 and len(out["items"]) == 50
    assert out["deep"]["a"]["b"]["c"]["d"] == "[TRUNCATED]"
    assert out["uid"] == "00000000-0000-0000-0000-000000000001"


def test_audit_pagination_and_filters(client, login, login_organizer, make_team):
    t = make_team()
    email = login_organizer()
    client.post(f"/admin/teams/{t}/credits/initial-grant", json={"amount": 100})
    for i in range(4):
        client.post(
            f"/admin/teams/{t}/credits",
            json={"amount": 1, "reason": f"bonus {i}", "idempotency_key": str(uuid.uuid4())},
        )
    p1 = client.get(f"/admin/audit?target_id={t}&limit=3").json()
    assert len(p1["items"]) == 3 and p1["next_cursor"] is not None
    p2 = client.get(f"/admin/audit?target_id={t}&limit=3&cursor={p1['next_cursor']}").json()
    assert len(p2["items"]) == 2 and p2["next_cursor"] is None
    by_actor = client.get(f"/admin/audit?actor_email={email.upper()}&action=credits.grant_initial").json()
    assert [r["target_id"] for r in by_actor["items"]] == [str(t)]


def test_other_modules_can_audit_their_own_actions(org_principal, db):
    """Team 2's round routes call audit(s, org, "market.round_open", ...) in their own transaction."""
    org = org_principal("OPERATOR")
    row = audit(db, org, "market.round_open", target_type="round", target_id="r2", reason="Round 2 start")
    assert row.id is not None and row.action == "market.round_open" and row.actor_email == org.email
    for bad in ("Round Open", "market", "market.", "x" * 61 + ".y"):
        with pytest.raises(ValueError):
            audit(db, org, bad, target_type="round", target_id="r2", reason="bad name")
    with pytest.raises(ValueError):
        audit(db, org, "market.round_open", target_type="round", target_id="r2", reason="   ")
