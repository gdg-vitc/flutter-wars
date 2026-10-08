"""Concurrency guarantees that only a real PostgreSQL can demonstrate
(row locks, partial unique indexes). Skipped on SQLite.

    TEST_DATABASE_URL=postgresql+psycopg://... uv run pytest
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
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
from app.modules.pricing.service import TradeSide
from tests.conftest import PG_URL, T0
from tests.test_trade_contract import DYNAMIC, buy, dynamic, finite, make_round

pytestmark = pytest.mark.skipif(
    not (PG_URL or "").startswith("postgresql"), reason="needs TEST_DATABASE_URL=postgresql..."
)


def attempt(engine: Engine, fn, *args) -> str:
    with Session(engine) as s:
        try:
            fn(s, *args)
            return "ok"
        except AppError as exc:
            s.rollback()
            return exc.code


def test_last_units_go_to_exactly_that_many_buyers(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=3))
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: attempt(engine, buy, listing.id, 1, T0), range(12)))
    assert sorted(results) == ["OUT_OF_STOCK"] * 9 + ["ok"] * 3
    session.expire_all()
    assert session.get(MarketListing, listing.id).stock_remaining == 0
    assert session.get(ListingPricing, listing.id).interval_bought == 3


def test_concurrent_buys_in_one_interval_pay_one_price(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=1000))
    now = T0 + timedelta(seconds=61)  # every buyer must first settle interval 0 -> 90
    prices: list[int] = []

    def one(_: int) -> None:
        with Session(engine) as s:
            prices.append(buy(s, listing.id, 2, now))

    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(one, range(20)))
    assert prices == [90] * 20
    session.expire_all()
    state = session.get(ListingPricing, listing.id)
    assert (state.interval_index, state.interval_bought) == (1, 40)
    # Interval 0 was settled exactly once despite 20 racing buyers.
    assert [h.interval_index for h in pricing.price_history(session, listing.id)] == [0, 1]


def test_close_waits_for_in_flight_purchase(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    rnd, (listing,) = make_round(session, widgets, finite(widgets[0], supply=5))
    locked, order = threading.Event(), []

    def slow_purchase() -> None:
        with Session(engine) as s:
            market.lock_listing_for_trade(s, listing.id, kind=RoundKind.TRADING)
            locked.set()
            time.sleep(0.5)
            pricing.get_current_price(s, listing.id, T0)
            market.take_stock(s, listing.id, 1)
            pricing.record_trade(s, listing.id, 1, TradeSide.BUY, T0)
            s.commit()
            order.append("purchase committed")

    def close() -> None:
        locked.wait()
        with Session(engine) as s:
            market.transition(s, rnd.id, "close", now=T0, actor="org")
            s.commit()
            order.append("round closed")

    threads = [threading.Thread(target=slow_purchase), threading.Thread(target=close)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert order == ["purchase committed", "round closed"]
    assert attempt(engine, buy, listing.id, 1, T0) == "ROUND_NOT_OPEN"
    session.expire_all()
    assert session.get(MarketListing, listing.id).stock_remaining == 4


def _race(
    engine: Engine, round_id: int, actions: list[str], expected_version: int | None
) -> list[str]:
    barrier = threading.Barrier(len(actions))

    def act(action: str) -> str:
        with Session(engine) as s:
            barrier.wait()
            try:
                market.transition(
                    s, round_id, action, now=T0, actor=action, expected_version=expected_version
                )
                s.commit()
                return "ok"
            except AppError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(act, actions))


def test_conflicting_organizer_transitions_serialise(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    """Transitions lock the round, so the second organizer acts on fresh state:
    pause-then-close is valid, close-then-pause is rejected. Never both lost."""
    for _ in range(5):
        rnd, _ = make_round(session, widgets, finite(widgets[0]))
        pause, close = _race(engine, rnd.id, ["pause", "close"], expected_version=None)
        assert close == "ok" and pause in ("ok", "INVALID_ROUND_TRANSITION")
        session.expire_all()
        stored = session.get(MarketRound, rnd.id)
        assert stored.status == "closed"
        assert stored.version == 2 + [pause, close].count("ok")
        assert len(market.round_events(session, rnd.id)) == 1 + [pause, close].count("ok")


def test_expected_version_lets_only_one_stale_view_win(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    for _ in range(5):
        rnd, _ = make_round(session, widgets, finite(widgets[0]))
        results = _race(engine, rnd.id, ["pause", "close"], expected_version=2)
        assert sorted(results) == ["ROUND_VERSION_CONFLICT", "ok"]
        session.expire_all()
        if (
            session.get(MarketRound, rnd.id).status == "paused"
        ):  # pause won; close it for the next round
            market.transition(session, rnd.id, "close", now=T0, actor="cleanup")
            session.commit()


def test_two_rounds_cannot_open_together(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    market.create_market(session, "M")
    ids = []
    for name in ("A", "B"):
        rnd = market.create_round(
            session, name=name, kind=RoundKind.TRADING, listings=[finite(widgets[0])]
        )
        ids.append(rnd.id)
    session.commit()
    barrier = threading.Barrier(2)

    def open_(round_id: int) -> str:
        with Session(engine) as s:
            barrier.wait()
            try:
                market.transition(s, round_id, "open", now=T0, actor="org")
                s.commit()
                return "ok"
            except AppError as exc:
                return exc.code

    for _ in range(5):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(pool.map(open_, ids))
        assert results == ["ANOTHER_ROUND_LIVE", "ok"]
        live = market.repo.live_round(session, session.get(MarketRound, ids[0]).market_id)
        # Reset for the next attempt: close the winner, re-draft both via fresh rounds.
        market.transition(session, live.id, "close", now=T0, actor="org")
        ids = [
            market.create_round(
                session, name=n, kind=RoundKind.TRADING, listings=[finite(widgets[0])]
            ).id
            for n in "CD"
        ]
        session.commit()
        barrier.reset()


def test_param_update_serialises_with_trades(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, (listing,) = make_round(session, widgets, dynamic(widgets[0], supply=1000))
    now = T0 + timedelta(seconds=30)

    def update() -> str:
        with Session(engine) as s:
            pricing.update_params(s, listing.id, DYNAMIC | {"price_step": 1}, now)
            s.commit()
            return "ok"

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(attempt, engine, buy, listing.id, 1, now) for _ in range(10)] + [
            pool.submit(update)
        ]
        assert all(f.result() == "ok" for f in futures)
    session.expire_all()
    state = session.get(ListingPricing, listing.id)
    assert (state.interval_bought, state.params_version, state.params["price_step"]) == (10, 2, 1)


def test_concurrent_draft_edits_stay_consistent(
    engine: Engine, session: Session, widgets: list[Widget]
) -> None:
    _, listings = make_round(session, widgets, finite(widgets[0]), finite(widgets[1]), open_at=None)
    target, doomed = listings
    round_id, target_id, doomed_id = target.round_id, target.id, doomed.id

    def edit(i: int) -> str:
        with Session(engine) as s:
            try:
                if i == 7:
                    market.delete_listing(s, doomed_id)
                elif i % 2:
                    market.update_listing(s, target_id, base_price=100 + i)
                else:
                    market.update_listing(s, target_id, max_per_purchase=i + 1)
                    market.update_listing(s, doomed_id, max_per_purchase=i + 1)
                s.commit()
                return "ok"
            except AppError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(edit, range(16)))
    # Edits to the deleted listing after its deletion fail cleanly; nothing deadlocks.
    assert set(results) <= {"ok", "LISTING_NOT_FOUND"}
    session.expire_all()
    stored = session.get(MarketListing, target_id)
    assert session.get(ListingPricing, target_id).current_price == stored.base_price
    assert market.repo.listing_ids(session, round_id) == [target_id]
