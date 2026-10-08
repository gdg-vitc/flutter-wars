"""Regression tests: a session that read rows earlier must not act on stale
copies after another session committed. Each test interleaves two sessions
deterministically (no threads), so they run on SQLite and PostgreSQL.

The identity map holds objects weakly, so each test keeps a reference to what
it "read earlier" (`_cached`), as a request handler would."""

from datetime import timedelta

import pytest
from sqlalchemy import Engine
from sqlmodel import Session

from app.core.errors import AppError
from app.modules.catalog.models import Widget
from app.modules.market import service as market
from app.modules.market.models import MarketListing, MarketRound, RoundKind
from app.modules.pricing import service as pricing
from app.modules.pricing.models import ListingPricing
from tests.conftest import T0
from tests.test_trade_contract import DYNAMIC, buy, dynamic, finite, make_round


def test_param_update_sees_demand_committed_after_it_read(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    with Session(engine) as organizer:
        _cached = pricing.get_config(organizer, listing.id)  # zero demand
        with Session(engine) as buyer:
            buy(buyer, listing.id, 20, T0 + timedelta(seconds=5))
        pricing.update_pricing(
            organizer, listing.id, strategy=None, params=DYNAMIC, now=T0 + timedelta(seconds=61)
        )
        organizer.commit()
        assert _cached is not None
    session.expire_all()
    assert session.get(ListingPricing, listing.id).current_price == 150


def test_param_update_rejected_after_concurrent_close(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    with Session(engine) as organizer:
        _cached = organizer.get(MarketRound, rnd.id)  # open
        with Session(engine) as other:
            market.transition(other, rnd.id, "close", now=T0 + timedelta(seconds=30), actor="other")
            other.commit()
        with pytest.raises(AppError) as exc:
            pricing.update_pricing(
                organizer,
                listing.id,
                strategy=None,
                params=DYNAMIC,
                now=T0 + timedelta(seconds=150),
            )
        assert exc.value.code == "PRICING_NOT_EDITABLE"


def test_draft_edit_rejected_after_concurrent_open(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0]), open_at=None)
    with Session(engine) as editor:
        _cached = (
            market.get_round(editor, rnd.id),
            market.get_listing(editor, listing.id),
        )  # draft
        with Session(engine) as opener:
            market.transition(opener, rnd.id, "open", now=T0, actor="opener")
            opener.commit()
        for attempt in (
            lambda: market.update_listing(editor, listing.id, base_price=1),
            lambda: market.delete_listing(editor, listing.id),
            lambda: market.add_listing(editor, rnd.id, finite(widgets[1])),
        ):
            with pytest.raises(AppError) as exc:
                attempt()
            assert exc.value.code == "ROUND_NOT_EDITABLE"
    session.expire_all()
    assert session.get(MarketListing, listing.id).base_price == 100


def test_trade_rejected_after_concurrent_pause(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0]))
    with Session(engine) as buyer:
        _cached = buyer.get(MarketRound, rnd.id)  # open
        with Session(engine) as organizer:
            market.transition(organizer, rnd.id, "pause", now=T0, actor="org")
            organizer.commit()
        with pytest.raises(AppError) as exc:
            buy(buyer, listing.id, 1, T0)
        assert exc.value.code == "ROUND_NOT_OPEN"


def test_quote_uses_fresh_stock_and_counters(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    with Session(engine) as reader:
        assert pricing.quote(reader, listing.id, T0).price == 100
        _cached = (
            reader.get(ListingPricing, listing.id),
            reader.get(MarketListing, listing.id),
            reader.get(MarketRound, rnd.id),
        )
        with Session(engine) as buyer:
            buy(buyer, listing.id, 20, T0 + timedelta(seconds=5))
        assert pricing.quote(reader, listing.id, T0 + timedelta(seconds=61)).price == 150
        assert (
            pricing.quote_many(reader, rnd.id, T0 + timedelta(seconds=61))[listing.id].price == 150
        )


def test_close_never_predates_a_committed_trade(session: Session, widgets: list[Widget]) -> None:
    rnd, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 30, T0)
    # This trade was stamped after the close request, but it got the round lock first.
    buy(session, listing.id, 1, T0 + timedelta(seconds=61))
    closed = market.transition(
        session, rnd.id, "close", now=T0 + timedelta(seconds=59), actor="org"
    )
    session.commit()
    assert closed.closed_at.replace(tzinfo=None) == (T0 + timedelta(seconds=61)).replace(
        tzinfo=None
    )
    quote = pricing.quote(session, listing.id, T0 + timedelta(hours=1))
    assert (quote.price, quote.interval_index, quote.valid_until) == (150, 1, None)


def test_close_cutoff_survives_out_of_order_trade_stamps(
    session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, dynamic(widgets[0]))
    buy(session, listing.id, 1, T0 + timedelta(seconds=61))
    buy(session, listing.id, 1, T0 + timedelta(seconds=58))  # stamped earlier, committed later
    closed = market.transition(
        session, rnd.id, "close", now=T0 + timedelta(seconds=59), actor="org"
    )
    session.commit()
    assert closed.closed_at.replace(tzinfo=None) == (T0 + timedelta(seconds=61)).replace(
        tzinfo=None
    )


def test_second_draft_editor_does_not_reset_price_from_stale_listing(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, (listing,) = make_round(session, widgets, finite(widgets[0]), open_at=None)
    with Session(engine) as b:
        _cached = b.get(MarketListing, listing.id)  # base 100
        with Session(engine) as a:
            market.update_listing(a, listing.id, base_price=200)
            a.commit()
        market.update_listing(b, listing.id, max_per_purchase=3)
        b.commit()
    session.expire_all()
    assert session.get(MarketListing, listing.id).base_price == 200
    assert session.get(ListingPricing, listing.id).current_price == 200


def test_trade_sees_listing_edited_before_open(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0]), open_at=None)
    with Session(engine) as buyer:
        _cached = buyer.get(MarketListing, listing.id)  # base 100, no limit
        with Session(engine) as organizer:
            market.update_listing(organizer, listing.id, base_price=250, max_per_purchase=1)
            market.transition(organizer, rnd.id, "open", now=T0, actor="org")
            organizer.commit()
        tradable = market.lock_listing_for_trade(buyer, listing.id, kind=RoundKind.TRADING)
        assert (tradable.base_price, tradable.max_per_purchase) == (250, 1)


def test_opening_records_committed_price(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0]), open_at=None)
    with Session(engine) as opener:
        _cached = opener.get(ListingPricing, listing.id)  # price 100
        with Session(engine) as editor:
            market.update_listing(editor, listing.id, base_price=180)
            editor.commit()
        market.transition(opener, rnd.id, "open", now=T0, actor="org")
        opener.commit()
    assert [(h.reason, h.price) for h in pricing.price_history(session, listing.id)] == [
        ("initial", 180)
    ]
