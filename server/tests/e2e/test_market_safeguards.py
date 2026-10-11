"""Adversarial event flows through real HTTP, JWT, gateways and PostgreSQL locks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from sqlalchemy import event
from sqlmodel import Session, select

from app.contracts.pricing import PricingGateway
from app.core.clock import get_now
from app.core.db import get_engine
from app.core.services import override
from app.modules.admin.models import AdminActionLog
from app.modules.authentication.model import Team
from app.modules.pricing.gateway import PricingGatewayImpl
from tests.e2e.test_competition_flow import _ok, _open_round, _setup_team, _widget


def _trade(client, member, listing, quantity, side="purchase", **limits):
    return client.post(
        f"/market/{side}",
        headers=member,
        json={"listing_id": listing, "quantity": quantity, "idempotency_key": str(uuid4()), **limits},
    )


def _dynamic_round(client, organizer, wid):
    rnd = _open_round(
        client,
        organizer,
        "trading",
        [
            {
                "widget_id": wid,
                "base_price": 100,
                "supply": 100,
                "pricing": {"strategy": "dynamic", "params": {"interval_seconds": 10}},
            }
        ],
    )
    return rnd, rnd["listings"][0]["id"]


def test_self_pump_cannot_farm_credits(app, client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login, credits=5000)
    wid = _widget(client, organizer)
    clock = [datetime.now(UTC)]
    app.dependency_overrides[get_now] = lambda: clock[0]
    with override(PricingGateway, lambda s: PricingGatewayImpl(s, now=lambda: clock[0])):
        _, listing = _dynamic_round(client, organizer, wid)
        for _ in range(6):
            before = _ok(client.get("/wallet", headers=member))["balance"]
            bought = _ok(_trade(client, member, listing, 20))
            clock[0] += timedelta(seconds=30)
            displayed = _ok(client.get(f"/market/listings/{listing}/price", headers=member))
            assert 98 <= displayed["price"] <= 102
            sold = _ok(_trade(client, member, listing, 20, side="sell"))
            assert sold["final_amount"] <= bought["final_amount"]
            assert _ok(client.get("/wallet", headers=member))["balance"] <= before
            clock[0] += timedelta(seconds=30)


def test_other_teams_demand_allows_small_profit_with_preview(app, client, organizer, team_login):
    _, early = _setup_team(client, organizer, team_login, credits=5000)
    _, later = _setup_team(client, organizer, team_login, credits=5000)
    wid = _widget(client, organizer)
    clock = [datetime.now(UTC)]
    app.dependency_overrides[get_now] = lambda: clock[0]
    with override(PricingGateway, lambda s: PricingGatewayImpl(s, now=lambda: clock[0])):
        _, listing = _dynamic_round(client, organizer, wid)
        clock[0] += timedelta(seconds=30)
        bought = _ok(_trade(client, early, listing, 10))
        assert bought["unit_price"] == 98
        _ok(_trade(client, later, listing, 20))
        clock[0] += timedelta(seconds=50)
        payload = {"listing_id": listing, "quantity": 10, "idempotency_key": str(uuid4())}
        preview = _ok(client.post("/market/resale-quote", headers=early, json=payload))
        assert preview["cost_basis"] == 980 and preview["external_demand_growth"] == 20
        sold = _ok(
            client.post("/market/sell", headers=early, json=payload | {"min_final_amount": preview["final_amount"]})
        )
        assert bought["final_amount"] < sold["final_amount"] <= bought["final_amount"] * 105 // 100
        assert sold["final_amount"] == preview["final_amount"]


def test_price_limit_refuses_trade_without_debit(client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login)
    rnd = _open_round(
        client, organizer, "trading", [{"widget_id": _widget(client, organizer), "base_price": 100, "supply": 10}]
    )
    listing = rnd["listings"][0]["id"]
    assert _trade(client, member, listing, 1, max_unit_price=99).status_code == 409
    assert _ok(client.get("/wallet", headers=member))["balance"] == 1000
    _ok(_trade(client, member, listing, 1))
    assert _trade(client, member, listing, 1, side="sell", min_final_amount=101).status_code == 409
    assert _ok(client.get("/wallet", headers=member))["balance"] == 900


def test_cost_basis_survives_new_listing_and_concurrent_resales(client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login, credits=5000)
    wid = _widget(client, organizer)
    first = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 100, "supply": 100}])
    _ok(_trade(client, member, first["listings"][0]["id"], 10))
    second = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 200, "supply": 100}])
    listing = second["listings"][0]["id"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: _trade(client, member, listing, 10, side="sell"), range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert _ok(client.get("/wallet", headers=member))["balance"] <= 5000


def test_pause_freezes_price_and_preserves_remaining_interval(app, client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login)
    wid = _widget(client, organizer)
    clock = [datetime.now(UTC)]
    app.dependency_overrides[get_now] = lambda: clock[0]
    with override(PricingGateway, lambda s: PricingGatewayImpl(s, now=lambda: clock[0])):
        rnd, listing = _dynamic_round(client, organizer, wid)
        _ok(_trade(client, member, listing, 5))
        clock[0] += timedelta(seconds=7)
        _ok(client.post(f"/admin/market/rounds/{rnd['id']}/pause", headers=organizer))
        paused = _ok(client.get(f"/market/listings/{listing}/price", headers=member))
        clock[0] += timedelta(minutes=10)
        frozen = _ok(client.get(f"/market/listings/{listing}/price", headers=member))
        assert (frozen["price"], frozen["interval_index"], frozen["valid_until"]) == (paused["price"], 0, None)
        _ok(client.post(f"/admin/market/rounds/{rnd['id']}/open", headers=organizer))
        resumed = _ok(client.get(f"/market/listings/{listing}/price", headers=member))
        assert datetime.fromisoformat(resumed["valid_until"]) == clock[0] + timedelta(seconds=3)


def test_disabled_team_ide_key_is_denied_and_key_actions_audited(client, organizer, team_login):
    tid, _ = _setup_team(client, organizer, team_login)
    key = _ok(client.post(f"/admin/teams/{tid}/api-keys", headers=organizer), 201)
    assert client.get("/ide/state", headers={"X-Team-API-Key": key["api_key"]}).status_code == 200
    with Session(get_engine()) as session:
        name = session.get(Team, tid).name
    _ok(
        client.post(
            f"/admin/teams/{tid}/status",
            headers=organizer,
            json={"status": "DISABLED", "reason": "Test disable", "confirm": name},
        )
    )
    assert client.get("/ide/state", headers={"X-Team-API-Key": key["api_key"]}).status_code == 403
    _ok(client.delete(f"/admin/teams/{tid}/api-keys/{key['key_id']}", headers=organizer))
    with Session(get_engine()) as s:
        actions = s.exec(select(AdminActionLog.action).where(AdminActionLog.target_id == str(tid))).all()
    assert "ide.api_key_issue" in actions and "ide.api_key_revoke" in actions


def test_one_connection_per_authenticated_purchase(client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login)
    rnd = _open_round(
        client, organizer, "trading", [{"widget_id": _widget(client, organizer), "base_price": 100, "supply": 10}]
    )
    checked_out = []

    def checkout(*args):
        checked_out.append(1)

    event.listen(get_engine(), "checkout", checkout)
    try:
        _ok(_trade(client, member, rnd["listings"][0]["id"], 1))
    finally:
        event.remove(get_engine(), "checkout", checkout)
    assert len(checked_out) == 1


def test_auction_discovery_manual_stop_cancel_refunds_and_exclusive_resale(client, organizer, team_login):
    _, member = _setup_team(client, organizer, team_login)
    wid = _widget(client, organizer)
    rnd = _open_round(client, organizer, "auction", [{"widget_id": wid, "base_price": 100, "supply": 3}])
    listing = rnd["listings"][0]["id"]
    now = datetime.now(UTC)

    def create():
        a = _ok(
            client.post(
                "/admin/auctions",
                headers=organizer,
                json={
                    "round_id": rnd["id"],
                    "listing_id": listing,
                    "widget_id": wid,
                    "quantity": 1,
                    "minimum_bid": 10,
                    "starts_at": (now - timedelta(seconds=5)).isoformat(),
                    "closes_at": (now + timedelta(hours=1)).isoformat(),
                },
            )
        )
        _ok(
            client.post(
                f"/admin/market/listings/{listing}/auction-lots",
                headers=organizer,
                json={"auction_id": a["id"], "quantity": 1},
            ),
            201,
        )
        return a

    auction = create()
    assert auction["id"] not in {a["id"] for a in _ok(client.get("/auctions", headers=member))}
    assert auction["id"] in {a["id"] for a in _ok(client.get("/admin/auctions", headers=organizer))}
    _ok(client.post(f"/admin/auctions/{auction['id']}/open", headers=organizer))
    _ok(
        client.post(
            f"/auctions/{auction['id']}/bids", headers=member, json={"amount": 70, "idempotency_key": str(uuid4())}
        )
    )
    assert client.delete(f"/admin/market/auction-lots/{auction['id']}", headers=organizer).status_code == 409
    _ok(client.post(f"/admin/auctions/{auction['id']}/cancel", headers=organizer))
    assert _ok(client.get("/wallet", headers=member))["held"] == 0
    # Retry cancel is safe; terminal auctions cannot pay twice.
    _ok(client.post(f"/admin/auctions/{auction['id']}/cancel", headers=organizer))
    assert client.post(f"/admin/auctions/{auction['id']}/settle", headers=organizer).status_code == 409
    second = create()
    _ok(client.post(f"/admin/auctions/{second['id']}/open", headers=organizer))
    _ok(
        client.post(
            f"/auctions/{second['id']}/bids", headers=member, json={"amount": 60, "idempotency_key": str(uuid4())}
        )
    )
    _ok(client.post(f"/admin/auctions/{second['id']}/close", headers=organizer))
    _ok(client.post(f"/admin/auctions/{second['id']}/settle", headers=organizer))
    normal = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 100, "supply": 10}])
    assert _trade(client, member, normal["listings"][0]["id"], 1, side="sell").status_code == 409


def test_zero_credit_team_can_redeem_real_free_inventory(client, organizer, team_login):
    tid, member = _setup_team(client, organizer, team_login, credits=0)
    wid = _widget(client, organizer)
    _ok(
        client.post(
            f"/admin/teams/{tid}/inventory/adjust",
            headers=organizer,
            json={"widget_id": wid, "delta": 2, "reason": "Starting free widgets", "idempotency_key": str(uuid4())},
        ),
        201,
    )
    rnd = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 100, "supply": 10}])
    sold = _ok(_trade(client, member, rnd["listings"][0]["id"], 2, side="sell"))
    assert sold["final_amount"] == 198
    assert _ok(client.get("/wallet", headers=member))["balance"] == 198
