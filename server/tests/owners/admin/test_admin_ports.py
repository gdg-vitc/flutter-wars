import uuid
from types import SimpleNamespace

from app.contracts.market import MarketGateway
from app.contracts.trading import TradingGateway, TransactionQuery
from app.core.services import override


def test_market_status_503_until_module_g_registers(client, login, login_organizer):
    login_organizer("VIEWER")
    with override(MarketGateway, None):
        assert client.get("/admin/market/status").status_code == 503
    with override(
        MarketGateway,
        lambda s: SimpleNamespace(status_summary=lambda: {"round": 2, "phase": "AUCTION", "open_listings": 7}),
    ):
        assert client.get("/admin/market/status").json() == {"round": 2, "phase": "AUCTION", "open_listings": 7}


def test_transaction_feed_gets_clamped_query(client, login, login_organizer):
    login_organizer("VIEWER")
    seen = []

    def feed(q):
        seen.append(q)
        return {"items": [{"id": "tx1"}], "next_cursor": None}

    with override(TradingGateway, lambda s: SimpleNamespace(transaction_feed=feed)):
        t = uuid.uuid4()
        r = client.get(f"/admin/transactions?team_id={t}&limit=100&cursor=abc")
        assert r.json()["items"] == [{"id": "tx1"}]
        assert seen[0] == TransactionQuery(team_id=t, limit=100, cursor="abc")
        assert client.get("/admin/transactions?limit=1000").status_code == 422


def test_transactions_503_until_module_i_registers(client, login, login_organizer):
    login_organizer()
    with override(TradingGateway, None):
        r = client.get("/admin/transactions")
        assert r.status_code == 503 and r.json()["error"]["context"] == {"module": "Module I transactions"}
