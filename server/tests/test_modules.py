from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.auction.models import Auction
from app.auction.schemas import AuctionView, BidRequest
from app.trading.errors import (
    AuctionNotOpen,
    BidNotIncreasing,
    ConfigurationRequired,
    IdempotencyConflict,
    InsufficientCredits,
    InsufficientInventory,
    InsufficientStock,
    InvalidListing,
    MarketNotOpen,
    ResaleNotAllowed,
)
from app.trading.schemas import PurchaseRequest, SellRequest


def buy(env, *, quantity=1, key=None, team=None):
    return env.runtime.purchase(
        team or env.team,
        PurchaseRequest(listing_id=env.listing, quantity=quantity, idempotency_key=key or uuid4()),
    )


def sell(env, *, quantity=1, key=None):
    return env.runtime.sell(
        env.team,
        SellRequest(listing_id=env.listing, quantity=quantity, idempotency_key=key or uuid4()),
    )


def bid(env, amount, *, team=None, key=None):
    return env.runtime.bid(
        team or env.team, env.auction, BidRequest(amount=amount, idempotency_key=key or uuid4())
    )


def test_purchase_records_server_price_and_all_effects(env):
    result = buy(env, quantity=2)
    assert (result.unit_price, result.gross_amount, result.final_amount) == (100, 200, 200)
    assert env.snapshot() == {"balance": 4800, "reserved": 0, "stock": 3, "owned": 2, "trades": 1}


@pytest.mark.parametrize("quantity", [0, -1, True, 1.5, "2", 2147483648])
def test_invalid_quantity(quantity):
    with pytest.raises(ValidationError):
        PurchaseRequest(listing_id=uuid4(), quantity=quantity, idempotency_key=uuid4())


def test_frontend_price_and_team_ignored(env):
    request = PurchaseRequest(
        listing_id=env.listing, quantity=1, idempotency_key=uuid4(), price=1, team_id=env.other_team
    )
    assert env.runtime.purchase(env.team, request).final_amount == 100
    assert env.snapshot()["balance"] == 4900


@pytest.mark.parametrize("state", ["CLOSED", "PAUSED"])
def test_round_rejects_purchase(env, state):
    env.set_round(state)
    before = env.snapshot()
    with pytest.raises(MarketNotOpen):
        buy(env)
    assert env.snapshot() == before


def test_invalid_listing(env):
    with pytest.raises(InvalidListing):
        env.runtime.purchase(
            env.team, PurchaseRequest(listing_id=uuid4(), quantity=1, idempotency_key=uuid4())
        )


def test_stock_failure(env):
    before = env.snapshot()
    with pytest.raises(InsufficientStock):
        buy(env, quantity=6)
    assert env.snapshot() == before


def test_credit_failure(env):
    env.set_balance(50)
    before = env.snapshot()
    with pytest.raises(InsufficientCredits):
        buy(env)
    assert env.snapshot() == before


def test_infinite_purchase_and_resale_rejection(env):
    env.set_infinite()
    assert buy(env, quantity=6).quantity == 6
    assert env.snapshot()["stock"] is None
    with pytest.raises(ResaleNotAllowed):
        sell(env)


def test_purchase_retry_after_close_returns_original(env):
    key = uuid4()
    original = buy(env, key=key)
    env.set_round("CLOSED")
    assert buy(env, key=key).id == original.id
    assert env.snapshot()["trades"] == 1
    with pytest.raises(IdempotencyConflict):
        buy(env, key=key, quantity=2)


def test_purchase_rollback_after_inventory_failure(env):
    env.fail_inventory = True
    before = env.snapshot()
    with pytest.raises(RuntimeError, match="injected"):
        buy(env)
    assert env.snapshot() == before


def test_finite_resale_uses_current_price_and_policy(env):
    buy(env, quantity=2)
    env.set_price(120)
    result = sell(env, quantity=2)
    assert (
        result.unit_price,
        result.gross_amount,
        result.brokerage_amount,
        result.final_amount,
    ) == (120, 240, 20, 220)
    assert env.snapshot() == {"balance": 5020, "reserved": 0, "stock": 5, "owned": 0, "trades": 2}


def test_resale_brokerage_sees_prior_resold_quantity(env):
    seen = []

    class Recording:
        def fee(self, *, prior_quantity, **_):
            seen.append(prior_quantity)
            return 0

    env.runtime.brokerage = Recording()
    buy(env, quantity=3)
    key = uuid4()
    sell(env, quantity=1, key=key)
    sell(env, quantity=2)
    sell(env, quantity=1, key=key)  # a retry returns the receipt and is not counted again
    assert seen == [0, 1]


def test_resale_retry(env):
    buy(env)
    key = uuid4()
    original = sell(env, key=key)
    assert sell(env, key=key).id == original.id
    assert env.snapshot()["trades"] == 2


def test_resale_without_inventory(env):
    before = env.snapshot()
    with pytest.raises(InsufficientInventory):
        sell(env)
    assert env.snapshot() == before


def test_resale_policy_required(env):
    buy(env)
    env.runtime.brokerage = None
    with pytest.raises(ConfigurationRequired):
        sell(env)


def test_resale_rollback(env):
    buy(env)
    env.fail_credit = True
    before = env.snapshot()
    with pytest.raises(RuntimeError, match="injected"):
        sell(env)
    assert env.snapshot() == before


def test_last_item_concurrency(env):
    env.set_stock(1)

    def run(team):
        try:
            return buy(env, team=team)
        except InsufficientStock:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, [env.team, env.other_team]))
    assert sum(x is not None for x in results) == 1
    assert env.total_owned() == 1
    assert env.snapshot()["stock"] == 0


def test_simultaneous_spending(env):
    env.set_balance(100)

    def run(_):
        try:
            return buy(env)
        except InsufficientCredits:
            return None

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(run, range(2)))
    assert sum(x is not None for x in results) == 1
    assert env.snapshot()["balance"] == 0


def test_concurrent_duplicate_purchase(env):
    env.set_stock(1)
    key = uuid4()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: buy(env, key=key), range(2)))
    assert results[0].id == results[1].id
    assert env.snapshot()["trades"] == 1


def test_first_bid_and_increase_reserve_only_difference(env):
    first = bid(env, 1000)
    second = bid(env, 1400)
    assert first.amount == 1000 and second.amount == 1400
    assert env.snapshot()["reserved"] == 1400
    assert env.snapshot()["balance"] == 5000


@pytest.mark.parametrize("amount", [1000, 900])
def test_non_increasing_bid(env, amount):
    bid(env, 1000)
    with pytest.raises(BidNotIncreasing):
        bid(env, amount)
    assert env.snapshot()["reserved"] == 1000


def test_bid_after_close(env):
    env.close_auction()
    with pytest.raises(AuctionNotOpen):
        bid(env, 1000)


def test_bid_insufficient_funds(env):
    with pytest.raises(InsufficientCredits):
        bid(env, 5001)
    assert env.snapshot()["reserved"] == 0


def test_bid_reservation_competes_with_purchase(env):
    bid(env, 4950)
    with pytest.raises(InsufficientCredits):
        buy(env)
    assert env.snapshot()["reserved"] == 4950


def test_bid_retry_after_later_increase_and_close(env):
    key = uuid4()
    first = bid(env, 1000, key=key)
    bid(env, 1400)
    env.close_auction()
    assert bid(env, 1000, key=key) == first
    assert env.snapshot()["reserved"] == 1400
    with pytest.raises(IdempotencyConflict):
        bid(env, 1100, key=key)


def test_participant_views_hide_competitors(env):
    bid(env, 1000, team=env.other_team)
    view = env.runtime.auction_view(env.team, env.auction)
    assert isinstance(view, AuctionView)
    assert "1000" not in view.model_dump_json()
    assert str(env.other_team) not in view.model_dump_json()
    assert env.runtime.my_bid(env.team, env.auction) is None


def test_settlement_ties_and_idempotency(env):
    bid(env, 1000)
    bid(env, 1000, team=env.other_team)
    env.close_auction()
    result = env.runtime.settle(env.auction)
    assert result.winner_team_id == env.team
    assert result.winning_amount == 1000
    assert env.snapshot()["balance"] == 4000
    assert env.snapshot()["reserved"] == 0
    assert env.other_reserved() == 0
    assert env.snapshot()["owned"] == 1
    assert env.runtime.settle(env.auction) == result
    assert env.snapshot()["owned"] == 1
    assert env.settlement_debits() == 1


def test_earliest_to_reach_final_amount_wins(env):
    bid(env, 500)
    bid(env, 1000, team=env.other_team)
    bid(env, 1000)
    env.close_auction()
    assert env.runtime.settle(env.auction).winner_team_id == env.other_team


def test_highest_final_bid_wins(env):
    bid(env, 500)
    bid(env, 850, team=env.other_team)
    bid(env, 1000)
    env.close_auction()
    assert env.runtime.settle(env.auction).winner_team_id == env.team


def test_settlement_rollback(env):
    bid(env, 1000)
    bid(env, 850, team=env.other_team)
    env.close_auction()
    env.fail_inventory = True
    before = env.snapshot()
    with pytest.raises(RuntimeError, match="injected"):
        env.runtime.settle(env.auction)
    assert env.snapshot() == before
    assert env.other_reserved() == 850
    env.fail_inventory = False
    assert env.runtime.settle(env.auction).winner_team_id == env.team


def test_concurrent_settlement(env):
    bid(env, 1000)
    env.close_auction()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: env.runtime.settle(env.auction), range(2)))
    assert results[0] == results[1]
    assert env.settlement_debits() == 1
    assert env.snapshot()["owned"] == 1


def test_concurrent_bid_increases(env):
    bid(env, 500)

    def run(amount):
        try:
            return bid(env, amount)
        except BidNotIncreasing:
            return None

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(run, [700, 1000]))
    assert env.runtime.my_bid(env.team, env.auction).amount == 1000
    assert env.snapshot()["reserved"] == 1000


def test_settle_before_deadline_rejected(env):
    bid(env, 1000)
    with pytest.raises(AuctionNotOpen):
        env.runtime.settle(env.auction)
    assert env.snapshot()["reserved"] == 1000


def test_purchase_ignores_forged_adapter_price_type(env):
    from dataclasses import replace

    from app.integration.errors import AmountTooLarge, InvalidPrice
    from tests.adapters import PricingAdapter

    original = env.adapters

    class BrokenPricing(PricingAdapter):
        def get_unit_price(self, **kwargs):
            return True

    env.runtime.adapter_factory = lambda s: replace(original(s), pricing=BrokenPricing(s))
    before = env.snapshot()
    with pytest.raises(InvalidPrice):
        buy(env)
    assert env.snapshot() == before
    env.runtime.adapter_factory = original
    env.set_price(2147483647)
    with pytest.raises(AmountTooLarge):
        buy(env, quantity=2)
    assert env.snapshot() == before


def test_bid_rolls_back_insufficient_increment(env):
    bid(env, 1000)
    with pytest.raises(InsufficientCredits):
        bid(env, 5001)
    assert env.runtime.my_bid(env.team, env.auction).amount == 1000
    assert env.snapshot()["reserved"] == 1000


def test_paused_round_rejects_bid(env):
    env.set_round("PAUSED")
    with pytest.raises(MarketNotOpen):
        bid(env, 1000)
    assert env.snapshot()["reserved"] == 0


def test_auction_only_listing_cannot_be_purchased(env):
    with pytest.raises(InvalidListing):
        env.runtime.purchase(
            env.team,
            PurchaseRequest(listing_id=env.auction_listing, quantity=1, idempotency_key=uuid4()),
        )


def test_auction_minimum_and_no_bid_policy_are_explicit(env):
    from app.integration.errors import BidBelowMinimum

    env.change(Auction.__table__, Auction.id == env.auction, minimum_bid=100)
    with pytest.raises(BidBelowMinimum):
        bid(env, 99)
    env.change(Auction.__table__, Auction.id == env.auction, minimum_bid=None)
    with pytest.raises(ConfigurationRequired):
        bid(env, 100)
    env.close_auction()
    with pytest.raises(ConfigurationRequired):
        env.runtime.settle(env.auction)


def test_bid_waiting_for_account_is_rejected_at_deadline(env):
    from dataclasses import replace
    from threading import Event

    from sqlalchemy import select, text

    from tests.adapters import LedgerAdapter, wallets

    reached = Event()
    original = env.adapters

    class ObservedLedger(LedgerAdapter):
        def lock_accounts(self, *, team_ids):
            reached.set()
            return super().lock_accounts(team_ids=team_ids)

    env.runtime.adapter_factory = lambda s: replace(original(s), ledger=ObservedLedger(s, env))
    with env.engine.begin() as c:
        c.execute(
            text(
                "UPDATE auction SET closes_at = clock_timestamp() + interval '0.25 seconds' WHERE id=:id"
            ),
            {"id": env.auction},
        )
    with env.engine.connect() as c:
        transaction = c.begin()
        c.execute(select(wallets).where(wallets.c.team_id == env.team).with_for_update())
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(bid, env, 1000)
            assert reached.wait(2)
            c.execute(text("SELECT pg_sleep(0.3)"))
            transaction.commit()
            with pytest.raises(AuctionNotOpen):
                future.result(timeout=5)
    assert env.snapshot()["reserved"] == 0


def test_bid_and_settlement_share_auction_guard(env):
    from dataclasses import replace
    from threading import Event

    from sqlalchemy import select, text

    from tests.adapters import LedgerAdapter, wallets

    bid(env, 1000)
    reached = Event()
    original = env.adapters

    class ObservedLedger(LedgerAdapter):
        def lock_accounts(self, *, team_ids):
            reached.set()
            return super().lock_accounts(team_ids=team_ids)

    env.runtime.adapter_factory = lambda s: replace(original(s), ledger=ObservedLedger(s, env))
    with env.engine.begin() as c:
        c.execute(
            text(
                "UPDATE auction SET closes_at = clock_timestamp() + interval '0.25 seconds' WHERE id=:id"
            ),
            {"id": env.auction},
        )
    with env.engine.connect() as c:
        transaction = c.begin()
        c.execute(select(wallets).where(wallets.c.team_id == env.team).with_for_update())
        with ThreadPoolExecutor(2) as pool:
            pending_bid = pool.submit(bid, env, 1400)
            assert reached.wait(2)
            c.execute(text("SELECT pg_sleep(0.3)"))
            pending_settle = pool.submit(env.runtime.settle, env.auction)
            transaction.commit()
            with pytest.raises(AuctionNotOpen):
                pending_bid.result(timeout=5)
            assert pending_settle.result(timeout=5).winning_amount == 1000
    assert env.snapshot()["balance"] == 4000
    assert env.snapshot()["reserved"] == 0


def test_current_bid_order_handles_identical_timestamps(env):
    from app.auction.models import Bid

    bid(env, 1000)
    bid(env, 1000, team=env.other_team)
    with env.engine.begin() as c:
        c.execute(Bid.__table__.update().values(amount_reached_at=datetime.now(timezone.utc)))
    env.close_auction()
    assert env.runtime.settle(env.auction).winner_team_id == env.team


def test_services_require_transaction(env):
    from sqlmodel import Session

    with Session(env.engine) as s:
        with pytest.raises(RuntimeError, match="transaction"):
            env.runtime._trading(s).purchase(
                team_id=env.team,
                request=PurchaseRequest(
                    listing_id=env.listing, quantity=1, idempotency_key=uuid4()
                ),
            )


def test_adapter_session_mismatch_rejected(env):
    from dataclasses import replace

    from sqlmodel import Session

    original = env.adapters
    with Session(env.engine) as other:
        env.runtime.adapter_factory = lambda s: replace(
            original(s), pricing=original(other).pricing
        )
        with pytest.raises(ValueError, match="shared session"):
            buy(env)


@pytest.mark.parametrize(
    "changes",
    [
        {"gross_amount": 101},
        {"final_amount": 99},
        {"brokerage_amount": 1, "final_amount": 99},
    ],
)
def test_database_rejects_inconsistent_trade_amounts(env, changes):
    from sqlalchemy.exc import IntegrityError

    from app.trading.models import TradeTransaction

    data = dict(
        id=uuid4(),
        team_id=env.team,
        listing_id=env.listing,
        widget_id=env.widget,
        idempotency_key=uuid4(),
        transaction_type="BUY",
        quantity=1,
        unit_price=100,
        gross_amount=100,
        brokerage_amount=0,
        final_amount=100,
        created_at=datetime.now(timezone.utc),
    )
    data.update(changes)
    with pytest.raises(IntegrityError):
        with env.engine.begin() as c:
            c.execute(TradeTransaction.__table__.insert().values(**data))


def test_database_requires_complete_winner_snapshot(env):
    from sqlalchemy.exc import IntegrityError

    from app.auction.models import AuctionResult

    with pytest.raises(IntegrityError):
        with env.engine.begin() as c:
            c.execute(
                AuctionResult.__table__.insert().values(
                    auction_id=env.auction,
                    winner_team_id=env.team,
                    winning_amount=None,
                    settled_at=datetime.now(timezone.utc),
                )
            )


def test_purchase_and_bid_share_available_balance(env):
    from threading import Barrier

    barrier = Barrier(2)

    def purchase():
        barrier.wait()
        try:
            return buy(env)
        except InsufficientCredits:
            return None

    def reserve():
        barrier.wait()
        try:
            return bid(env, 4950)
        except InsufficientCredits:
            return None

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(purchase), pool.submit(reserve)]
        results = [f.result(timeout=5) for f in futures]
    assert sum(result is not None for result in results) == 1
    state = env.snapshot()
    assert state["balance"] - state["reserved"] >= 0


def test_round_close_waits_for_guarded_purchase(env):
    from concurrent.futures import TimeoutError
    from dataclasses import replace
    from threading import Event

    from tests.adapters import InventoryAdapter

    started, release, closing = Event(), Event(), Event()
    original = env.adapters

    class PausingInventory(InventoryAdapter):
        def add(self, **kwargs):
            super().add(**kwargs)
            started.set()
            if not release.wait(3):
                raise RuntimeError("test synchronization timed out")

    env.runtime.adapter_factory = lambda s: replace(original(s), inventory=PausingInventory(s, env))

    def close():
        closing.set()
        env.set_round("CLOSED")

    with ThreadPoolExecutor(2) as pool:
        purchase = pool.submit(buy, env)
        assert started.wait(2)
        closure = pool.submit(close)
        assert closing.wait(2)
        try:
            with pytest.raises(TimeoutError):
                closure.result(timeout=0.05)
        finally:
            release.set()
        assert purchase.result(timeout=5).quantity == 1
        closure.result(timeout=5)
    with pytest.raises(MarketNotOpen):
        buy(env)


def test_commit_failure_does_not_return_success_or_leave_effects(env):
    from sqlalchemy import event
    from sqlmodel import Session

    before = env.snapshot()

    def fail_commit(session):
        raise RuntimeError("injected commit failure")

    event.listen(Session, "before_commit", fail_commit)
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            buy(env)
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert env.snapshot() == before


def test_bid_receipt_failure_rolls_back_reservation_and_bid(env, monkeypatch):
    from app.auction.models import BidReceipt
    from app.auction.repository import AuctionRepository

    original = AuctionRepository.add

    def failing_add(repo, item):
        original(repo, item)
        if isinstance(item, BidReceipt):
            raise RuntimeError("injected receipt failure")

    monkeypatch.setattr(AuctionRepository, "add", failing_add)
    with pytest.raises(RuntimeError, match="receipt failure"):
        bid(env, 1000)
    assert env.runtime.my_bid(env.team, env.auction) is None
    assert env.snapshot()["reserved"] == 0


def test_settlement_result_failure_rolls_back_every_effect(env, monkeypatch):
    from app.auction.models import AuctionResult
    from app.auction.repository import AuctionRepository

    bid(env, 1000)
    bid(env, 850, team=env.other_team)
    env.close_auction()
    before = env.snapshot()
    original = AuctionRepository.add

    def failing_add(repo, item):
        original(repo, item)
        if isinstance(item, AuctionResult):
            raise RuntimeError("injected result failure")

    monkeypatch.setattr(AuctionRepository, "add", failing_add)
    with pytest.raises(RuntimeError, match="result failure"):
        env.runtime.settle(env.auction)
    assert env.snapshot() == before
    assert env.other_reserved() == 850
    assert env.settlement_debits() == 0


def test_concurrent_duplicate_bid_reserves_once(env):
    key = uuid4()
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: bid(env, 1000, key=key), range(2)))
    assert results[0] == results[1]
    assert env.snapshot()["reserved"] == 1000


def test_pricing_effect_failure_rolls_back_trade_and_repricing(env):
    from dataclasses import replace

    from sqlalchemy import select, update

    from tests.adapters import PricingAdapter, listings

    original = env.adapters

    class FailingPriceEffects(PricingAdapter):
        def record_trade(self, **kwargs):
            self.session.execute(
                update(listings).where(listings.c.id == kwargs["listing_id"]).values(price=101)
            )
            raise RuntimeError("injected pricing effect failure")

    env.runtime.adapter_factory = lambda s: replace(original(s), pricing=FailingPriceEffects(s))
    before = env.snapshot()
    with pytest.raises(RuntimeError, match="pricing effect failure"):
        buy(env)
    assert env.snapshot() == before
    with env.engine.connect() as c:
        assert (
            c.execute(select(listings.c.price).where(listings.c.id == env.listing)).scalar_one()
            == 100
        )
