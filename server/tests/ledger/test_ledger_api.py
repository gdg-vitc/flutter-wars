from sqlmodel import Session

from app.core.db import engine
from app.modules import ledger


def _fund(team, amount=120):
    with Session(engine) as s:
        ledger.grant_initial(s, team, amount, actor="test")
        s.commit()


def test_participant_sees_only_own_wallet(client, login, make_team):
    a, b = make_team(), make_team()
    _fund(a, 120)
    _fund(b, 50)
    login(team_id=a)
    r = client.get("/wallet")
    assert r.status_code == 200
    assert r.json() == {"team_id": str(a), "balance": 120, "held": 0, "available": 120}


def test_team_cannot_be_chosen_by_the_client(client, login, make_team):
    """There is no team_id parameter at all: query strings are ignored, the token decides."""
    a, b = make_team(), make_team()
    _fund(b, 77)
    login(team_id=a)
    assert client.get(f"/wallet?team_id={b}").json()["team_id"] == str(a)


def test_no_login_is_rejected(client):
    assert client.get("/wallet").status_code == 501  # TEMP stub: becomes 401 once Module B lands


def test_login_without_team_is_rejected(client, login):
    login(email="someone@gmail.test")  # a real Google user who is not in any team
    r = client.get("/wallet")
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_A_TEAM_MEMBER"


def test_ledger_pagination_hides_actor(client, login, make_team):
    t = make_team()
    _fund(t, 120)
    with Session(engine) as s:
        for i in range(5):
            ledger.debit(s, t, 1, ref_type="purchase", ref_id=f"p{i}", reason="r", actor="secret@org")
        s.commit()
    login(team_id=t)
    page1 = client.get("/wallet/ledger?limit=4").json()
    assert len(page1["items"]) == 4 and page1["next_cursor"] is not None
    assert "actor" not in page1["items"][0]
    page2 = client.get(f"/wallet/ledger?limit=4&cursor={page1['next_cursor']}").json()
    assert len(page2["items"]) == 2 and page2["next_cursor"] is None
