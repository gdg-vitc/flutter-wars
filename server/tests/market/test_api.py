from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.auction.models import Auction, AuctionState
from app.contracts.principal import Principal
from app.core.db import get_db
from app.core.principal import get_principal
from app.main import create_app
from app.modules.admin.authz import require_organizer
from app.modules.admin.errors import NotOrganizer
from tests.market.conftest import ORGANIZER_GRANT
from tests.market.test_modules import bid


def _app(env):
    app = create_app()
    app.state.backend_runtime = env.runtime  # stand-in owner adapters

    def session():
        with Session(env.engine) as s:
            yield s

    app.dependency_overrides[get_db] = session
    return app


def client(env, *, team=None, admin=False):
    """A participant of `team` (default env.team), or an organizer granted by Module K."""
    app = _app(env)
    principal = (
        Principal(user_id="organizer", role="organizer", email=ORGANIZER_GRANT.email)
        if admin
        else Principal(user_id="player", team_id=team or env.team, email="player@example.test")
    )

    def organizer():
        if not admin:
            raise NotOrganizer()
        return ORGANIZER_GRANT

    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[require_organizer] = organizer
    return TestClient(app, raise_server_exceptions=False)


def test_purchase_http_and_team_scope(env):
    c = client(env)
    response = c.post(
        "/market/purchase",
        json=dict(
            listing_id=str(env.listing),
            quantity=1,
            idempotency_key=str(uuid4()),
            price=1,
            team_id=str(env.other_team),
        ),
    )
    assert response.status_code == 200
    assert response.json()["final_amount"] == 100
    trade = response.json()["id"]
    assert client(env, team=env.other_team).get(f"/transactions/{trade}").status_code == 404
    assert c.get("/transactions").json()[0]["id"] == trade


def test_admin_settlement_is_protected(env):
    bid(env, 1000)
    env.close_auction()
    assert client(env).post(f"/admin/auctions/{env.auction}/settle").status_code == 403
    assert client(env, admin=True).post(f"/admin/auctions/{env.auction}/settle").status_code == 200


def test_no_authentication_fallback(env):
    c = TestClient(_app(env))
    assert c.get("/transactions").status_code == 401


def test_hidden_bid_http(env):
    bid(env, 1234, team=env.other_team)
    c = client(env)
    response = c.get(f"/auctions/{env.auction}")
    assert response.status_code == 200
    assert "1234" not in response.text
    assert str(env.other_team) not in response.text
    assert c.get(f"/auctions/{env.auction}/my-bid").json() is None


def test_safe_business_error(env):
    env.set_balance(0)
    response = client(env).post(
        "/market/purchase",
        json=dict(listing_id=str(env.listing), quantity=1, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INSUFFICIENT_CREDITS"


def test_failure_response_is_safe_and_rolls_back(env):
    env.fail_inventory = True
    before = env.snapshot()
    response = client(env).post(
        "/market/purchase",
        json=dict(listing_id=str(env.listing), quantity=1, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 500
    assert "injected" not in response.text
    assert "Traceback" not in response.text
    assert env.snapshot() == before


def test_invalid_http_quantity_and_bid(env):
    c = client(env)
    for amount in (True, "100", 1.5, -1):
        response = c.post(f"/auctions/{env.auction}/bids", json=dict(amount=amount, idempotency_key=str(uuid4())))
        assert response.status_code == 422
    response = c.post(
        "/market/purchase",
        json=dict(listing_id=str(env.listing), quantity=0, idempotency_key=str(uuid4())),
    )
    assert response.status_code == 422


def test_own_bid_returns_only_authenticated_team(env):
    bid(env, 1000)
    bid(env, 1200, team=env.other_team)
    response = client(env).get(f"/auctions/{env.auction}/my-bid")
    assert response.status_code == 200
    assert response.json()["amount"] == 1000
    assert "1200" not in response.text
    assert "team_id" not in response.json()
    assert "amount_reached_order" not in response.json()


def test_public_api_has_no_competitor_history_or_result_route(env):
    c = client(env)
    assert c.get(f"/auctions/{env.auction}/bids").status_code == 405
    assert c.get(f"/auctions/{env.auction}/result").status_code == 404


def test_draft_requires_allocated_stock_before_open(env):
    c = client(env, admin=True)
    response = c.post(
        "/admin/auctions",
        json=dict(
            round_id=str(env.round),
            listing_id=str(env.auction_listing),
            widget_id=str(env.widget),
            quantity=1,
            starts_at="2026-01-01T00:00:00Z",
            closes_at="2099-01-01T00:00:00Z",
        ),
    )
    assert response.status_code == 200
    auction_id = response.json()["id"]
    assert response.json()["state"] == "DRAFT"
    assert c.post(f"/admin/auctions/{auction_id}/open").status_code == 503
    assert c.patch(f"/admin/auctions/{auction_id}/minimum-bid", json={"amount": 100}).status_code == 200
    # Configuration is not an allocation: open still cannot invent stock.
    assert c.post(f"/admin/auctions/{auction_id}/open").status_code == 503


def test_admin_configuration_is_forbidden_to_participants(env):
    c = client(env)
    for path in ("open", "close"):
        assert c.post(f"/admin/auctions/{env.auction}/{path}").status_code == 403
    assert c.patch(f"/admin/auctions/{env.auction}/minimum-bid", json={"amount": 100}).status_code == 403


def test_draft_auction_is_hidden_from_participants(env):
    draft = uuid4()
    with Session(env.engine) as s, s.begin():
        s.add(
            Auction(
                id=draft,
                round_id=env.round,
                listing_id=env.auction_listing,
                widget_id=env.widget,
                quantity=1,
                state=AuctionState.DRAFT,
                minimum_bid=4242,
                starts_at=datetime.now(timezone.utc),
                closes_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
        )
    c = client(env)
    for path in (f"/auctions/{draft}", f"/auctions/{draft}/my-bid"):
        response = c.get(path)
        assert response.status_code == 404
        assert response.json() == c.get(f"/auctions/{uuid4()}").json()
        assert "4242" not in response.text
