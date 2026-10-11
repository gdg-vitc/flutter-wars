"""Exercises the internal contracts Modules I (Transaction) and J (Auction)
will call, the way docs/market.md and docs/pricing.md tell them to."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session

from app.core.errors import AppError
from app.modules.catalog.models import Widget
from app.modules.market import service as market
from app.modules.market.models import MarketListing, RoundKind
from app.modules.pricing import service as pricing
from app.modules.pricing.models import ListingPricing
from app.modules.pricing.service import TradeSide
from tests.market.conftest import T0

DYNAMIC = {"interval_seconds": 60, "target_fraction": "0.1"}


def buy(session: Session, listing_id: int, quantity: int, now: datetime) -> int:
    """What Module I's purchase does with G/H, minus ledger and inventory."""
    market.lock_listing_for_trade(session, listing_id, kind=RoundKind.TRADING)
    quote = pricing.get_current_price(session, listing_id, now)
    market.take_stock(session, listing_id, quantity)
    pricing.record_trade(session, listing_id, quantity, TradeSide.BUY, now)
    session.commit()
    return quote.price


def sell(session: Session, listing_id: int, quantity: int, now: datetime) -> int:
    market.lock_listing_for_trade(session, listing_id, kind=RoundKind.TRADING)
    quote = pricing.get_current_price(session, listing_id, now)
    market.return_stock(session, listing_id, quantity)
    pricing.record_trade(session, listing_id, quantity, TradeSide.SELL, now)
    session.commit()
    return quote.price


def make_round(
    session: Session,
    widgets: list[Widget],
    *specs: market.ListingSpec,
    kind=RoundKind.TRADING,
    open_at=T0,
):
    if market.repo.active_market(session) is None:
        market.create_market(session, "Flutter Wars")
    rnd = market.create_round(session, name="R", kind=kind, listings=list(specs))
    session.commit()
    if open_at is not None:
        market.transition(session, rnd.id, "open", now=open_at, actor="org")
        session.commit()
    return rnd, market.listings_for_round(session, rnd.id)


def finite(widget: Widget, supply: int = 100, price: int = 100, **pricing_kwargs) -> market.ListingSpec:
    return market.ListingSpec(widget_id=widget.id, base_price=price, supply=supply, **pricing_kwargs)


def dynamic(widget: Widget, supply: int = 100, price: int = 100, params=DYNAMIC) -> market.ListingSpec:
    return finite(widget, supply, price, pricing_strategy="dynamic", pricing_params=params)


def code_of(fn, *args, **kwargs) -> str:
    with pytest.raises(AppError) as exc:
        fn(*args, **kwargs)
    return exc.value.code


# --- market gate ---


def test_round_state_is_checked_inside_mutation(session: Session, widgets: list[Widget]) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0]), open_at=None)
    # Draft listings do not exist as far as trading is concerned.
    assert code_of(market.lock_listing_for_trade, session, listing.id, kind=RoundKind.TRADING) == "LISTING_NOT_FOUND"
    session.rollback()
    market.transition(session, rnd.id, "open", now=T0, actor="org")
    session.commit()
    assert buy(session, listing.id, 1, T0) == 100
    for action, status in (("pause", "paused"), ("close", "closed")):
        market.transition(session, rnd.id, action, now=T0, actor="org")
        session.commit()
        with pytest.raises(AppError) as exc:
            buy(session, listing.id, 1, T0)
        assert exc.value.code == "ROUND_NOT_OPEN" and exc.value.context == {"status": status}
        session.rollback()


def test_unknown_listing(session: Session) -> None:
    assert code_of(market.lock_listing_for_trade, session, uuid4(), kind=RoundKind.TRADING) == "LISTING_NOT_FOUND"


def test_round_kind_must_match(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, finite(widgets[0]), kind=RoundKind.AUCTION)
    assert code_of(market.lock_listing_for_trade, session, listing.id, kind=RoundKind.TRADING) == "WRONG_ROUND_KIND"
    ok = market.lock_listing_for_trade(session, listing.id, kind=RoundKind.AUCTION)
    assert ok.round_kind == RoundKind.AUCTION and ok.stock_remaining == 100


# --- stock ---


def test_finite_stock_cannot_be_oversold(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, finite(widgets[0], supply=3))
    buy(session, listing.id, 2, T0)
    with pytest.raises(AppError) as exc:
        buy(session, listing.id, 2, T0)
    assert exc.value.code == "OUT_OF_STOCK" and exc.value.context == {"available": 1}
    session.rollback()
    buy(session, listing.id, 1, T0)
    assert session.get(MarketListing, listing.id).stock_remaining == 0
    assert code_of(buy, session, listing.id, 1, T0) == "OUT_OF_STOCK"


def test_infinite_stock(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, finite(widgets[0], supply="infinite"))  # type: ignore[arg-type]
    for _ in range(3):
        assert buy(session, listing.id, 1000, T0) == 100
    session.expire_all()
    stored = session.get(MarketListing, listing.id)
    assert stored.stock_remaining is None and stored.supply_total is None
    assert market.return_stock(session, listing.id, 5) is None


def test_quantity_rules(session: Session, widgets: list[Widget]) -> None:
    spec = market.ListingSpec(widget_id=widgets[0].id, base_price=10, supply=50, max_per_purchase=2)
    _, (listing,) = make_round(session, widgets, spec)
    assert code_of(market.take_stock, session, listing.id, 3) == "QUANTITY_LIMIT_EXCEEDED"
    assert code_of(market.take_stock, session, listing.id, 0) == "INVALID_QUANTITY"
    assert code_of(pricing.record_trade, session, listing.id, 0, TradeSide.BUY, T0) == "INVALID_QUANTITY"
    assert market.take_stock(session, listing.id, 2) == 48


def test_failed_purchase_changes_nothing(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=10))
    later = T0 + timedelta(seconds=150)
    market.lock_listing_for_trade(session, listing.id, kind=RoundKind.TRADING)
    pricing.get_current_price(session, listing.id, later)  # settles 2 intervals tentatively
    market.take_stock(session, listing.id, 4)
    pricing.record_trade(session, listing.id, 4, TradeSide.BUY, later)
    session.rollback()  # e.g. the ledger debit failed

    state = session.get(ListingPricing, listing.id)
    assert (state.current_price, state.interval_index, state.interval_bought) == (100, 0, 0)
    assert session.get(MarketListing, listing.id).stock_remaining == 10
    assert [h.reason for h in pricing.price_history(session, listing.id)] == ["initial"]


# --- pricing ---


def test_static_price_never_drifts(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, finite(widgets[0], supply=1000))
    for minute in range(0, 600, 37):
        assert buy(session, listing.id, 50, T0 + timedelta(minutes=minute)) == 100
    assert pricing.quote(session, listing.id, T0 + timedelta(days=1)).valid_until is None


def test_dynamic_price_follows_demand(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=100))
    # Net retained demand raises the price by at most one credit per boundary.
    assert buy(session, listing.id, 15, T0 + timedelta(seconds=5)) == 100
    assert buy(session, listing.id, 5, T0 + timedelta(seconds=59)) == 100  # same interval, same price
    assert buy(session, listing.id, 1, T0 + timedelta(seconds=60)) == 101
    # Retained units remain demand, even without another large purchase.
    quote = pricing.quote(session, listing.id, T0 + timedelta(seconds=125))
    assert (quote.price, quote.interval_index) == (102, 2)
    assert quote.valid_until == T0 + timedelta(seconds=180)

    history = pricing.price_history(session, listing.id)
    assert [(h.reason, h.interval_index, h.price, h.demand, h.supply) for h in history] == [
        ("initial", 0, 100, None, None),
        ("interval", 1, 101, 20, 100),
    ]


def test_quiet_market_decays_to_floor_and_stops(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    # An empty market eases to the narrow floor, then stops.
    prices = [pricing.quote(session, listing.id, T0 + timedelta(seconds=60 * k)).price for k in range(6)]
    assert prices == [100, 99, 98, 98, 98, 98]
    # A day of silence settles instantly instead of looping per interval.
    q = pricing.get_current_price(session, listing.id, T0 + timedelta(days=1))
    assert q.price == 98 and q.interval_index == 1440


def test_quote_is_read_only(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    assert pricing.quote(session, listing.id, T0 + timedelta(minutes=10)).price == 98
    session.expire_all()
    assert session.get(ListingPricing, listing.id).interval_index == 0


def test_lazy_settlement_matches_eager(session: Session, widgets: list[Widget]) -> None:
    """Prices depend only on committed trades, not on when they are observed."""
    _, (a, b) = make_round(session, widgets, dynamic(widgets[0]), dynamic(widgets[1]))
    trades = [(5, 30), (70, 4), (75, 11), (200, 2), (410, 25)]
    for second, qty in trades:
        buy(session, a.id, qty, T0 + timedelta(seconds=second))
        buy(session, b.id, qty, T0 + timedelta(seconds=second))
        # Observe `a` every second, `b` never between trades.
        for s in range(second, second + 30):
            pricing.recalculate(session, a.id, T0 + timedelta(seconds=s))
        session.commit()
    end = T0 + timedelta(seconds=900)
    assert pricing.get_current_price(session, a.id, end).price == pricing.get_current_price(session, b.id, end).price
    assert [(h.interval_index, h.price) for h in pricing.price_history(session, a.id)] == [
        (h.interval_index, h.price) for h in pricing.price_history(session, b.id)
    ]


def test_sales_reduce_demand(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 20, T0)
    sell(session, listing.id, 15, T0 + timedelta(seconds=10))
    # net 5 vs target (supply at start 100 * 0.1) -> ratio 0.5 -> x1.0
    assert pricing.quote(session, listing.id, T0 + timedelta(seconds=60)).price == 100
    assert session.get(MarketListing, listing.id).stock_remaining == 95


def test_sold_out_listing_holds_price(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=10))
    buy(session, listing.id, 10, T0)
    assert pricing.quote(session, listing.id, T0 + timedelta(seconds=60)).price == 101
    assert pricing.quote(session, listing.id, T0 + timedelta(minutes=30)).price == 102


def test_closing_freezes_price(session: Session, widgets: list[Widget]) -> None:
    rnd, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 30, T0)
    market.transition(session, rnd.id, "close", now=T0 + timedelta(seconds=90), actor="org")
    session.commit()
    quote = pricing.quote(session, listing.id, T0 + timedelta(hours=5))
    assert (quote.price, quote.interval_index, quote.valid_until) == (101, 1, None)


def test_param_change_mid_round(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 20, T0)
    later = T0 + timedelta(seconds=130)
    # Elapsed intervals settle under the old parameters (100 -> 101 -> 102).
    pricing.update_params(session, listing.id, DYNAMIC | {"max_factor": "1.01"}, later)
    session.commit()
    state = session.get(ListingPricing, listing.id)
    assert (state.current_price, state.interval_index, state.params_version) == (102, 2, 2)
    # The tightened bound applies at the next boundary.
    assert pricing.quote(session, listing.id, T0 + timedelta(seconds=180)).price == 101

    assert (
        code_of(pricing.update_params, session, listing.id, DYNAMIC | {"interval_seconds": 30}, later)
        == "PRICING_INTERVAL_LOCKED"
    )
    session.rollback()
    assert code_of(pricing.update_params, session, listing.id, {"bogus": 1}, later) == "INVALID_PRICING_PARAMS"
    session.rollback()
    reasons = [h.reason for h in pricing.price_history(session, listing.id)]
    assert reasons == ["initial", "interval", "interval", "config_change"]


def test_old_history_is_not_rewritten(session: Session, widgets: list[Widget]) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 20, T0)
    pricing.recalculate(session, listing.id, T0 + timedelta(seconds=60))
    session.commit()
    before = [(h.interval_index, h.price) for h in pricing.price_history(session, listing.id)]
    pricing.update_params(
        session,
        listing.id,
        DYNAMIC | {"target_fraction": "0.2", "max_factor": "1.01"},
        T0 + timedelta(seconds=61),
    )
    session.commit()
    after = [(h.interval_index, h.price) for h in pricing.price_history(session, listing.id)]
    assert after[: len(before)] == before


def test_api_pricing_endpoints(api, widgets: list[Widget], clock) -> None:
    from tests.market.conftest import ORGANIZER, PARTICIPANT

    api.post("/admin/market", json={"name": "M"})
    rnd = api.post(
        "/admin/market/rounds",
        json={
            "name": "R",
            "kind": "trading",
            "listings": [
                {
                    "widget_id": str(widgets[0].id),
                    "base_price": 100,
                    "supply": 100,
                    "pricing": {"strategy": "dynamic", "params": DYNAMIC},
                },
            ],
        },
    ).json()
    listing_id = rnd["listings"][0]["id"]
    url = f"/admin/market/listings/{listing_id}/pricing"

    # Draft: strategy may change.
    assert api.patch(url, json={"strategy": "static"}).json()["strategy"] == "static"
    assert api.patch(url, json={"strategy": "dynamic", "params": DYNAMIC}).json()["params_version"] == 3
    api.post(f"/admin/market/rounds/{rnd['id']}/open")

    clock.advance(seconds=61)
    api.as_(PARTICIPANT)
    price = api.get(f"/market/listings/{listing_id}/price").json()
    assert price["price"] == 99 and price["interval_index"] == 1
    assert price["valid_until"].startswith("2026-10-12T09:02:00")

    api.as_(ORGANIZER)
    locked = api.patch(url, json={"strategy": "static"})
    assert locked.status_code == 409 and locked.json()["error"]["code"] == "PRICING_STRATEGY_LOCKED"
    updated = api.patch(url, json={"params": DYNAMIC | {"price_step": 1}}).json()
    assert updated["current_price"] == 99 and updated["interval_index"] == 1
    history = api.get(f"/admin/market/listings/{listing_id}/price-history").json()
    assert [h["reason"] for h in history] == ["initial", "interval", "config_change"]

    api.post(f"/admin/market/rounds/{rnd['id']}/close")
    closed = api.patch(url, json={"params": DYNAMIC})
    assert closed.status_code == 409 and closed.json()["error"]["code"] == "PRICING_NOT_EDITABLE"
    assert api.get(f"/admin/market/listings/{listing_id}/pricing").json()["strategy"] == "dynamic"
