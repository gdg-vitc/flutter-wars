import uuid

import pytest

from app.modules.admin import ports


@pytest.fixture()
def restore_ports():
    saved = (ports.market_status_provider(), ports.transaction_feed())
    yield
    ports.set_market_status_provider(saved[0])
    ports.set_transaction_feed(saved[1])


def test_market_status_503_until_module_g_registers(client, login, login_organizer, restore_ports):
    login_organizer("VIEWER")
    ports.set_market_status_provider(None)
    assert client.get("/admin/market/status").status_code == 503
    ports.set_market_status_provider(lambda s: {"round": 2, "phase": "AUCTION", "open_listings": 7})
    assert client.get("/admin/market/status").json() == {"round": 2, "phase": "AUCTION", "open_listings": 7}


def test_transaction_feed_gets_clamped_query(client, login, login_organizer, restore_ports):
    login_organizer("VIEWER")
    seen = []

    def feed(s, q):
        seen.append(q)
        return {"items": [{"id": "tx1"}], "next_cursor": None}

    ports.set_transaction_feed(feed)
    t = uuid.uuid4()
    r = client.get(f"/admin/transactions?team_id={t}&limit=100&cursor=abc")
    assert r.json()["items"] == [{"id": "tx1"}]
    assert seen[0] == ports.TransactionQuery(team_id=t, limit=100, cursor="abc")
    assert client.get("/admin/transactions?limit=1000").status_code == 422


def test_transactions_503_until_module_i_registers(client, login, login_organizer, restore_ports):
    login_organizer()
    ports.set_transaction_feed(None)
    r = client.get("/admin/transactions")
    assert r.status_code == 503 and r.json()["error"]["details"] == {"module": "Module I transactions"}
