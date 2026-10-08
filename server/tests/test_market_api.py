import pytest

from app.modules.catalog.models import Widget
from tests.conftest import ORGANIZER, PARTICIPANT, Api

MISSING = "00000000-0000-4000-8000-000000000000"


def error_code(response) -> str:
    return response.json()["error"]["code"]


def setup_market(api: Api) -> None:
    assert api.post("/admin/market", json={"name": "Flutter Wars"}).status_code == 201


def create_round(api: Api, widgets: list[Widget], **overrides) -> dict:
    body = {
        "name": "Round 1",
        "kind": "trading",
        "listings": [
            {"widget_id": str(widgets[0].id), "base_price": 100, "supply": 10},
            {
                "widget_id": str(widgets[1].id),
                "base_price": 50,
                "supply": "infinite",
                "max_per_purchase": 3,
            },
        ],
    } | overrides
    response = api.post("/admin/market/rounds", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def act(api: Api, round_id: int, action: str, **body):
    return api.post(f"/admin/market/rounds/{round_id}/{action}", json=body or None)


# --- authorization ---


def test_unauthenticated_requests_are_rejected(api: Api) -> None:
    api.as_(None)
    for path in ("/market", "/market/listings", "/market/rounds/current", "/admin/market/rounds"):
        response = api.get(path)
        assert response.status_code == 401
        assert error_code(response) == "UNAUTHENTICATED"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/admin/market"),
        ("get", "/admin/market/rounds"),
        ("post", "/admin/market/rounds"),
        ("get", "/admin/market/rounds/00000000-0000-4000-8000-000000000000"),
        ("post", "/admin/market/rounds/00000000-0000-4000-8000-000000000000/open"),
        ("post", "/admin/market/rounds/00000000-0000-4000-8000-000000000000/pause"),
        ("post", "/admin/market/rounds/00000000-0000-4000-8000-000000000000/close"),
        ("post", "/admin/market/rounds/00000000-0000-4000-8000-000000000000/finalize"),
        ("post", "/admin/market/rounds/00000000-0000-4000-8000-000000000000/listings"),
        ("patch", "/admin/market/listings/00000000-0000-4000-8000-000000000000"),
        ("delete", "/admin/market/listings/00000000-0000-4000-8000-000000000000"),
        ("get", "/admin/market/listings/1/pricing"),
        ("patch", "/admin/market/listings/1/pricing"),
        ("get", "/admin/market/listings/1/price-history"),
        ("get", "/admin/market/pricing-strategies"),
    ],
)
def test_participants_cannot_use_admin_routes(api: Api, method: str, path: str) -> None:
    api.as_(PARTICIPANT)
    response = getattr(api, method)(path, **({} if method in ("get", "delete") else {"json": {}}))
    assert response.status_code == 403
    assert error_code(response) == "FORBIDDEN"


# --- market + round lifecycle ---


def test_market_summary_before_setup(api: Api) -> None:
    api.as_(PARTICIPANT)
    body = api.get("/market").json()
    assert body["market"] is None and body["current_round"] is None
    assert api.get("/market/listings").json()["listings"] == []
    assert error_code(api.get("/market/rounds/current")) == "NO_CURRENT_ROUND"


def test_only_one_active_market(api: Api) -> None:
    setup_market(api)
    response = api.post("/admin/market", json={"name": "Second"})
    assert response.status_code == 409
    assert error_code(response) == "MARKET_ALREADY_ACTIVE"


def test_round_requires_market(api: Api, widgets: list[Widget]) -> None:
    response = api.post("/admin/market/rounds", json={"name": "R", "kind": "trading"})
    assert response.status_code == 404
    assert error_code(response) == "NO_ACTIVE_MARKET"


def test_full_lifecycle(api: Api, widgets: list[Widget], clock) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    assert rnd["status"] == "draft" and rnd["sequence"] == 1 and rnd["version"] == 1
    listings = {item["widget_id"]: item for item in rnd["listings"]}
    finite, infinite = listings[str(widgets[0].id)], listings[str(widgets[1].id)]
    assert finite["stock_remaining"] == 10 and not finite["infinite_supply"]
    assert (
        infinite["infinite_supply"]
        and infinite["stock_remaining"] is None
        and infinite["supply_total"] is None
    )
    assert finite["pricing"]["strategy"] == "static"

    # Drafts are invisible to participants.
    api.as_(PARTICIPANT)
    assert api.get("/market").json()["current_round"] is None
    assert api.get("/market/listings").json() == {
        "round": None,
        "listings": [],
        "server_time": clock.now.isoformat().replace("+00:00", "Z"),
    }
    assert error_code(api.get(f"/market/listings/{finite['id']}/price")) == "LISTING_NOT_FOUND"

    api.as_(ORGANIZER)
    opened = act(api, rnd["id"], "open", reason="go")
    assert opened.status_code == 200, opened.text
    assert opened.json()["status"] == "open" and opened.json()["version"] == 2

    api.as_(PARTICIPANT)
    summary = api.get("/market").json()
    assert summary["market"]["name"] == "Flutter Wars"
    assert summary["current_round"]["status"] == "open"
    body = api.get("/market/listings").json()
    assert body["round"]["id"] == rnd["id"]
    shown = {item["widget_id"]: item for item in body["listings"]}
    assert shown[str(widgets[0].id)]["price"]["amount"] == 100
    assert shown[str(widgets[0].id)]["widget_name"] == "Button"
    assert shown[str(widgets[1].id)]["price"]["valid_until"] is None
    assert "pricing" not in shown[str(widgets[0].id)]  # admin-only config
    price = api.get(f"/market/listings/{finite['id']}/price").json()
    assert price["price"] == 100 and price["strategy"] == "static"

    api.as_(ORGANIZER)
    clock.advance(minutes=5)
    assert act(api, rnd["id"], "pause").json()["status"] == "paused"
    assert act(api, rnd["id"], "open").json()["status"] == "open"  # resume keeps opened_at
    assert act(api, rnd["id"], "close").json()["status"] == "closed"
    final = act(api, rnd["id"], "finalize").json()
    assert final["status"] == "finalized" and final["version"] == 6
    assert final["opened_at"].startswith("2026-10-12T09:00")

    events = api.get(f"/admin/market/rounds/{rnd['id']}/events").json()
    assert [(e["action"], e["from_status"], e["to_status"]) for e in events] == [
        ("open", "draft", "open"),
        ("pause", "open", "paused"),
        ("open", "paused", "open"),
        ("close", "open", "closed"),
        ("finalize", "closed", "finalized"),
    ]
    assert events[0]["actor"] == ORGANIZER.subject and events[0]["reason"] == "go"

    # Closed rounds stay visible read-only.
    api.as_(PARTICIPANT)
    assert api.get("/market/rounds/current").json()["status"] == "finalized"


@pytest.mark.parametrize(
    ("setup", "action"),
    [
        ([], "pause"),
        ([], "close"),
        ([], "finalize"),
        (["open"], "finalize"),
        (["open"], "open"),
        (["open", "pause"], "pause"),
        (["open", "close"], "open"),
        (["open", "close"], "pause"),
        (["open", "close", "finalize"], "close"),
    ],
)
def test_invalid_transitions_rejected(
    api: Api, widgets: list[Widget], setup: list[str], action: str
) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    for step in setup:
        assert act(api, rnd["id"], step).status_code == 200
    response = act(api, rnd["id"], action)
    assert response.status_code == 409
    assert error_code(response) == "INVALID_ROUND_TRANSITION"


def test_unknown_round(api: Api) -> None:
    assert error_code(act(api, MISSING, "open")) == "ROUND_NOT_FOUND"


def test_round_without_listings_cannot_open(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    rnd = create_round(api, widgets, listings=[])
    response = act(api, rnd["id"], "open")
    assert response.status_code == 409
    assert error_code(response) == "ROUND_HAS_NO_LISTINGS"


def test_only_one_live_round(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    first = create_round(api, widgets)
    second = create_round(api, widgets, name="Round 2")
    assert second["sequence"] == 2
    act(api, first["id"], "open")
    act(api, first["id"], "pause")
    response = act(api, second["id"], "open")
    assert response.status_code == 409
    assert error_code(response) == "ANOTHER_ROUND_LIVE"
    act(api, first["id"], "close")
    assert act(api, second["id"], "open").status_code == 200


def test_stale_expected_version_rejected(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    assert act(api, rnd["id"], "open", expected_version=1).status_code == 200
    # A second organizer still looking at version 1 tries to pause.
    response = act(api, rnd["id"], "pause", expected_version=1)
    assert response.status_code == 409
    assert error_code(response) == "ROUND_VERSION_CONFLICT"
    assert act(api, rnd["id"], "pause", expected_version=2).status_code == 200


def test_schedule_must_be_ordered(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    response = api.post(
        "/admin/market/rounds",
        json={
            "name": "R",
            "kind": "trading",
            "scheduled_open_at": "2026-10-12T10:00:00Z",
            "scheduled_close_at": "2026-10-12T09:00:00Z",
        },
    )
    assert error_code(response) == "INVALID_SCHEDULE"


# --- listings ---


def test_listing_validation(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    rnd = create_round(api, widgets, listings=[])
    url = f"/admin/market/rounds/{rnd['id']}/listings"
    ok = api.post(url, json={"widget_id": str(widgets[2].id), "base_price": 10, "supply": 0})
    assert ok.status_code == 201 and ok.json()["sold_out"] is True

    assert (
        error_code(
            api.post(url, json={"widget_id": str(widgets[2].id), "base_price": 10, "supply": 5})
        )
        == "DUPLICATE_LISTING"
    )
    assert (
        error_code(api.post(url, json={"widget_id": MISSING, "base_price": 10, "supply": 5}))
        == "WIDGET_NOT_FOUND"
    )
    assert (
        error_code(
            api.post(url, json={"widget_id": str(widgets[4].id), "base_price": 10, "supply": 5})
        )
        == "WIDGET_ARCHIVED"
    )
    for bad in (
        {"base_price": 0, "supply": 5},
        {"base_price": 10, "supply": -1},
        {"base_price": 10, "supply": "lots"},
        {"base_price": 10, "supply": 5, "price": 1},
    ):
        assert api.post(url, json={"widget_id": str(widgets[3].id)} | bad).status_code == 422
    dynamic_infinite = api.post(
        url,
        json={
            "widget_id": str(widgets[3].id),
            "base_price": 10,
            "supply": "infinite",
            "pricing": {"strategy": "dynamic"},
        },
    )
    assert error_code(dynamic_infinite) == "PRICING_REQUIRES_FINITE_SUPPLY"
    unknown = api.post(
        url,
        json={
            "widget_id": str(widgets[3].id),
            "base_price": 10,
            "supply": 1,
            "pricing": {"strategy": "x"},
        },
    )
    assert error_code(unknown) == "UNKNOWN_PRICING_STRATEGY"


def test_draft_listing_edit_and_delete(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    listing = rnd["listings"][0]
    url = f"/admin/market/listings/{listing['id']}"

    edited = api.patch(
        url, json={"base_price": 120, "supply": "infinite", "max_per_purchase": 2}
    ).json()
    assert (
        edited["base_price"] == 120
        and edited["infinite_supply"]
        and edited["max_per_purchase"] == 2
    )
    assert edited["price"]["amount"] == 120

    edited = api.patch(
        url,
        json={"supply": 4, "pricing": {"strategy": "dynamic", "params": {"interval_seconds": 60}}},
    ).json()
    assert edited["stock_remaining"] == 4
    assert (
        edited["pricing"]["strategy"] == "dynamic"
        and edited["pricing"]["params"]["interval_seconds"] == 60
    )

    # Changing only the price keeps the dynamic configuration.
    edited = api.patch(url, json={"base_price": 80}).json()
    assert edited["pricing"]["params"]["interval_seconds"] == 60 and edited["price"]["amount"] == 80

    edited = api.patch(url, json={"max_per_purchase": None}).json()
    assert edited["max_per_purchase"] is None

    assert api.delete(url).status_code == 204
    assert len(api.get(f"/admin/market/rounds/{rnd['id']}").json()["listings"]) == 1


def test_listings_frozen_after_open(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    act(api, rnd["id"], "open")
    listing = rnd["listings"][0]
    for response in (
        api.patch(f"/admin/market/listings/{listing['id']}", json={"base_price": 1}),
        api.delete(f"/admin/market/listings/{listing['id']}"),
        api.post(
            f"/admin/market/rounds/{rnd['id']}/listings",
            json={"widget_id": str(widgets[2].id), "base_price": 1, "supply": 1},
        ),
    ):
        assert response.status_code == 409
        assert error_code(response) == "ROUND_NOT_EDITABLE"


def test_archived_widget_blocks_open(api: Api, widgets: list[Widget], session) -> None:
    setup_market(api)
    rnd = create_round(api, widgets)
    widget = session.get(Widget, widgets[0].id)
    widget.archived = True
    session.commit()
    response = act(api, rnd["id"], "open")
    assert error_code(response) == "ROUND_HAS_ARCHIVED_WIDGETS"
    assert response.json()["error"]["context"]["widget_ids"] == [str(widgets[0].id)]


def test_widget_in_several_rounds_keeps_history(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    first = create_round(api, widgets)
    act(api, first["id"], "open")
    act(api, first["id"], "close")
    second = create_round(
        api,
        widgets,
        name="Round 2",
        listings=[
            {"widget_id": str(widgets[0].id), "base_price": 300, "supply": 2},
        ],
    )
    api.patch(f"/admin/market/listings/{second['listings'][0]['id']}", json={"base_price": 350})

    old = api.get(f"/admin/market/rounds/{first['id']}").json()
    button = next(item for item in old["listings"] if item["widget_id"] == str(widgets[0].id))
    assert button["base_price"] == 100 and button["supply_total"] == 10

    api.as_(PARTICIPANT)
    # Round 2 is still a draft, so participants keep seeing round 1.
    assert api.get("/market/rounds/current").json()["id"] == first["id"]


def test_admin_lists_rounds_and_strategies(api: Api, widgets: list[Widget]) -> None:
    setup_market(api)
    create_round(api, widgets)
    create_round(api, widgets, name="Auction", kind="auction")
    rounds = api.get("/admin/market/rounds").json()
    assert [(r["sequence"], r["kind"]) for r in rounds] == [(1, "trading"), (2, "auction")]
    keys = [s["key"] for s in api.get("/admin/market/pricing-strategies").json()]
    assert keys == ["dynamic", "static"]


def test_health(api: Api) -> None:
    assert api.get("/health").json() == {"status": "ok"}
