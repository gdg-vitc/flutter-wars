"""A competition run through every module, over HTTP only."""

import time
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4


def _ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


def _setup_team(client, organizer, team_login, credits=1000):
    email = f"member-{uuid4().hex[:8]}@student.test"
    team = _ok(
        client.post(
            "/admin/teams",
            json={"name": f"Team {uuid4().hex[:8]}", "member_emails": [email], "initial_credits": credits},
            headers=organizer,
        ),
        201,
    )
    team_id = UUID(team["id"])
    return team_id, team_login(team_id, email)


def _widget(client, organizer):
    wid = f"w_{uuid4().hex[:10]}"
    _ok(
        client.post(
            "/admin/widgets",
            json={"id": wid, "appdev_key": f"appdev.{wid}", "display_name": "Button", "category": "input"},
            headers=organizer,
        ),
        201,
    )
    return wid


def _open_round(client, organizer, kind, listings):
    status = _ok(client.get("/admin/market/status", headers=organizer))
    if status["market"] is None:
        _ok(client.post("/admin/market", json={"name": "Flutter Wars"}, headers=organizer), 201)
    elif status["round"] and status["round"]["status"] in ("open", "paused"):
        _ok(client.post(f"/admin/market/rounds/{status['round']['id']}/close", headers=organizer))
    rnd = _ok(
        client.post(
            "/admin/market/rounds",
            json={"name": f"{kind} round", "kind": kind, "listings": listings},
            headers=organizer,
        ),
        201,
    )
    _ok(client.post(f"/admin/market/rounds/{rnd['id']}/open", headers=organizer))
    return rnd


def test_login_identity_and_organizer_boundaries(client, organizer, team_login):
    team_id, member = _setup_team(client, organizer, team_login)
    me = _ok(client.get("/auth/me", headers=member))
    assert me["team_id"] == str(team_id) and me["role"] == "participant"
    assert _ok(client.get("/auth/me", headers=organizer))["team_id"] is None
    assert client.get("/admin/teams", headers=member).status_code == 403  # Module K decides
    assert client.post("/market/purchase", json={}, headers=organizer).status_code == 403  # no team
    assert client.get("/wallet").status_code == 401


def test_purchase_resale_freeze_and_ide_sync(client, organizer, team_login):
    team_id, member = _setup_team(client, organizer, team_login, credits=1000)
    wid = _widget(client, organizer)
    rnd = _open_round(client, organizer, "trading", [{"widget_id": wid, "base_price": 100, "supply": 10}])
    listing = next(x for x in _ok(client.get("/market/listings", headers=member))["listings"] if x["widget_id"] == wid)

    bought = _ok(
        client.post(
            "/market/purchase",
            json={"listing_id": listing["id"], "quantity": 2, "idempotency_key": str(uuid4())},
            headers=member,
        )
    )
    assert (bought["unit_price"], bought["final_amount"]) == (100, 200)
    assert _ok(client.get("/wallet", headers=member))["balance"] == 800  # Module E
    inventory = _ok(client.get("/inventory", headers=member))  # Module F
    assert any(i["widget_id"] == wid and i["quantity"] == 2 for i in inventory["items"])

    sold = _ok(
        client.post(
            "/market/sell",
            json={"listing_id": listing["id"], "quantity": 1, "idempotency_key": str(uuid4())},
            headers=member,
        )
    )
    assert sold["final_amount"] == 100 - sold["brokerage_amount"]
    assert _ok(client.get("/wallet", headers=member))["balance"] == 800 + sold["final_amount"]
    assert [t["id"] for t in _ok(client.get("/transactions", headers=member))][:2] == [sold["id"], bought["id"]]

    # Module K: organizer dashboard reads Module I's feed; an emergency freeze stops trading.
    feed = _ok(client.get(f"/admin/transactions?team_id={team_id}", headers=organizer))
    assert {t["id"] for t in feed["items"]} >= {bought["id"], sold["id"]}
    _ok(client.put("/admin/controls/TRADING", json={"frozen": True, "reason": "Break"}, headers=organizer))
    frozen = client.post(
        "/market/purchase",
        json={"listing_id": listing["id"], "quantity": 1, "idempotency_key": str(uuid4())},
        headers=member,
    )
    assert frozen.status_code == 423, frozen.text
    _ok(client.put("/admin/controls/TRADING", json={"frozen": False, "reason": "Resume"}, headers=organizer))

    # Module C: the IDE sees the team's inventory through its API key.
    key = _ok(client.post(f"/admin/teams/{team_id}/api-keys", headers=organizer), 201)["api_key"]
    state = _ok(client.get("/ide/state", headers={"X-Team-API-Key": key}))
    assert {"widget_id": wid, "quantity": 1} in state["widgets"]
    _ok(client.post(f"/admin/market/rounds/{rnd['id']}/close", headers=organizer))


def test_auction_bids_settlement_and_refunds(client, organizer, team_login):
    winner_id, winner = _setup_team(client, organizer, team_login, credits=500)
    loser_id, loser = _setup_team(client, organizer, team_login, credits=500)
    wid = _widget(client, organizer)
    rnd = _open_round(client, organizer, "auction", [{"widget_id": wid, "base_price": 50, "supply": 3}])
    listing = _ok(client.get(f"/admin/market/rounds/{rnd['id']}", headers=organizer))["listings"][0]

    now = datetime.now(UTC)
    auction = _ok(
        client.post(
            "/admin/auctions",
            json={
                "round_id": rnd["id"],
                "listing_id": listing["id"],
                "widget_id": wid,
                "quantity": 1,
                "starts_at": (now - timedelta(seconds=5)).isoformat(),
                "closes_at": (now + timedelta(seconds=4)).isoformat(),
                "minimum_bid": 10,
            },
            headers=organizer,
        )
    )
    assert client.get(f"/auctions/{auction['id']}", headers=winner).status_code == 404  # DRAFT is hidden
    _ok(
        client.post(
            f"/admin/market/listings/{listing['id']}/auction-lots",
            json={"auction_id": auction["id"], "quantity": 1},
            headers=organizer,
        ),
        201,
    )
    _ok(client.post(f"/admin/auctions/{auction['id']}/open", headers=organizer))

    def bid(headers, amount):
        return _ok(
            client.post(
                f"/auctions/{auction['id']}/bids",
                json={"amount": amount, "idempotency_key": str(uuid4())},
                headers=headers,
            )
        )

    bid(loser, 40)
    bid(winner, 60)
    assert _ok(client.get("/wallet", headers=winner))["held"] == 60  # Module E reservation
    assert _ok(client.get("/wallet", headers=loser))["held"] == 40
    with_loser = _ok(client.get(f"/auctions/{auction['id']}", headers=loser))
    # Compare the public response fields: random UUIDs can contain the digits "60".
    assert set(with_loser) == {
        "id",
        "listing_id",
        "widget_id",
        "quantity",
        "state",
        "starts_at",
        "closes_at",
        "minimum_bid",
    }

    time.sleep(max(0.0, (now + timedelta(seconds=4.5) - datetime.now(UTC)).total_seconds()))
    _ok(client.post(f"/admin/auctions/{auction['id']}/close", headers=organizer))
    result = _ok(client.post(f"/admin/auctions/{auction['id']}/settle", headers=organizer))
    assert result["winner_team_id"] == str(winner_id) and result["winning_amount"] == 60

    winner_wallet = _ok(client.get("/wallet", headers=winner))
    assert (winner_wallet["balance"], winner_wallet["held"]) == (440, 0)
    loser_wallet = _ok(client.get("/wallet", headers=loser))
    assert (loser_wallet["balance"], loser_wallet["held"]) == (500, 0)
    inventory = _ok(client.get("/inventory", headers=winner))
    assert any(i["widget_id"] == wid and i["quantity"] == 1 for i in inventory["items"])
    assert loser_id != winner_id
