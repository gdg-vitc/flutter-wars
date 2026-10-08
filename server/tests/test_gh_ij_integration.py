"""I/J (trading, auction) running on the REAL Market (G) and Pricing (H) adapters.

Ledger (E), Inventory (F) and Catalog (D) are still the test stand-ins from
tests/adapters.py. PostgreSQL only (uses the `ij_engine` fixture).
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlmodel import Session, SQLModel

import app.models  # noqa: F401  (registers every table)
from app.auction.models import Auction
from app.auction.schemas import AuctionCreate, BidRequest
from app.integration.contracts import Adapters
from app.integration.errors import BusinessError
from app.integration.runtime import BackendModules
from app.modules.catalog.models import Widget
from app.modules.market import service as market
from app.modules.market.adapter import MarketAdapter, release_unsold_lot
from app.modules.market.models import MarketAuctionLot, MarketListing, RoundKind
from app.modules.pricing.adapter import PricingAdapter
from app.modules.pricing.models import ListingPricing
from app.trading.schemas import PurchaseRequest, SellRequest
from tests.adapters import (
    CatalogAdapter,
    FixedTestBrokerage,
    InventoryAdapter,
    LedgerAdapter,
    catalog,
    inventory,
    metadata,
    wallets,
)


class World:
    def __init__(self, engine, kind: RoundKind, supply=5, price=100):
        self.engine = engine
        self.team, self.other_team = uuid4(), uuid4()
        self.fail_inventory = self.fail_credit = False
        with Session(engine) as s:
            widgets = [Widget(name="Button"), Widget(name="Card")]
            s.add_all(widgets)
            s.flush()
            market.create_market(s, "Flutter Wars")
            rnd = market.create_round(
                s,
                name="R1",
                kind=kind,
                listings=[
                    market.ListingSpec(widget_id=widgets[0].id, base_price=price, supply=supply),
                    market.ListingSpec(widget_id=widgets[1].id, base_price=7, supply="infinite"),
                ],
            )
            market.transition(s, rnd.id, "open", now=datetime.now(UTC), actor="test")
            widget_ids = [w.id for w in widgets]
            self.round, self.widget = rnd.id, widget_ids[0]
            self.listing, self.infinite = (
                market.repo.listing_for_widget(s, rnd.id, w).id for w in widget_ids
            )
            s.commit()
        with engine.begin() as c:
            c.execute(catalog.insert(), [{"id": w} for w in widget_ids])
            c.execute(
                wallets.insert(),
                [dict(team_id=t, balance=5000, reserved=0) for t in (self.team, self.other_team)],
            )
        self.runtime = BackendModules(
            session_factory=lambda: Session(engine),
            adapter_factory=self.adapters,
            brokerage=FixedTestBrokerage(),
            no_bid_handler=release_unsold_lot,
        )

    def adapters(self, s):
        return Adapters(
            market=MarketAdapter(s),
            pricing=PricingAdapter(s),
            ledger=LedgerAdapter(s, self),
            inventory=InventoryAdapter(s, self),
            catalog=CatalogAdapter(s),
        )

    def stock(self, listing=None):
        with Session(self.engine) as s:
            return s.get(MarketListing, listing or self.listing).stock_remaining

    def demand(self):
        with Session(self.engine) as s:
            state = s.get(ListingPricing, self.listing)
            return state.interval_bought, state.interval_sold

    def balance(self, team=None):
        with self.engine.connect() as c:
            return c.execute(
                select(wallets.c.balance).where(wallets.c.team_id == (team or self.team))
            ).scalar_one()

    def owned(self, team=None):
        with self.engine.connect() as c:
            return (
                c.execute(
                    select(inventory.c.quantity).where(
                        inventory.c.team_id == (team or self.team),
                        inventory.c.widget_id == self.widget,
                    )
                ).scalar_one_or_none()
                or 0
            )

    def buy(self, quantity=1, listing=None, team=None):
        return self.runtime.purchase(
            team or self.team,
            PurchaseRequest(
                listing_id=listing or self.listing, quantity=quantity, idempotency_key=uuid4()
            ),
        )

    def sell(self, quantity=1, listing=None):
        return self.runtime.sell(
            self.team,
            SellRequest(
                listing_id=listing or self.listing, quantity=quantity, idempotency_key=uuid4()
            ),
        )

    def transition(self, action):
        with Session(self.engine) as s:
            market.transition(s, self.round, action, now=datetime.now(UTC), actor="test")
            s.commit()


def code(fn, *args, **kwargs) -> str:
    with pytest.raises(BusinessError) as info:
        fn(*args, **kwargs)
    return info.value.code


@pytest.fixture
def fresh(ij_engine):
    SQLModel.metadata.drop_all(ij_engine)
    metadata.drop_all(ij_engine)
    SQLModel.metadata.create_all(ij_engine)
    metadata.create_all(ij_engine)
    yield ij_engine
    SQLModel.metadata.drop_all(ij_engine)
    metadata.drop_all(ij_engine)


@pytest.fixture
def trading(fresh):
    return World(fresh, RoundKind.TRADING)


@pytest.fixture
def auctions(fresh):
    return World(fresh, RoundKind.AUCTION, supply=3)


# --- trading through G/H ---


def test_purchase_uses_g_stock_and_h_price(trading):
    trade = trading.buy(2)
    assert (trade.unit_price, trade.final_amount) == (100, 200)
    assert trading.stock() == 3
    assert trading.demand() == (2, 0)  # H counted the demand in the same commit
    assert (trading.balance(), trading.owned()) == (4800, 2)


def test_purchase_rejections_map_to_port_errors(trading):
    assert code(trading.buy, 6) == "INSUFFICIENT_STOCK"
    assert code(trading.buy, 1, listing=uuid4()) == "INVALID_LISTING"
    trading.transition("pause")
    assert code(trading.buy, 1) == "MARKET_NOT_OPEN"
    trading.transition("open")
    assert trading.buy(1).quantity == 1


def test_auction_round_listing_cannot_be_bought(auctions):
    assert code(auctions.buy, 1) == "INVALID_LISTING"


def test_failure_after_all_mutations_rolls_back_g_and_h(trading):
    trading.fail_inventory = True
    with pytest.raises(RuntimeError):
        trading.buy(2)
    assert (trading.stock(), trading.demand(), trading.balance()) == (5, (0, 0), 5000)


def test_resale_returns_stock_and_counts_supply(trading):
    trading.buy(3)
    sale = trading.sell(2)
    assert sale.transaction_type == "SELL"
    assert trading.stock() == 4
    assert trading.demand() == (3, 2)
    assert trading.owned() == 1


def test_infinite_listing_rejects_resale(trading):
    trading.buy(1, listing=trading.infinite)
    assert code(trading.sell, 1, listing=trading.infinite) == "RESALE_NOT_ALLOWED"


def test_last_units_go_to_exactly_that_many_buyers(trading):
    teams = [trading.team, trading.other_team] * 4

    def attempt(team):
        try:
            trading.buy(1, team=team)
            return True
        except BusinessError as exc:
            assert exc.code == "INSUFFICIENT_STOCK"
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, teams))
    assert sum(results) == 5
    assert trading.stock() == 0
    assert trading.demand() == (5, 0)


# --- auctions through G's lots ---


def _auction(world, quantity=1, minimum_bid=10):
    now = datetime.now(UTC)
    return world.runtime.create_auction(
        AuctionCreate(
            round_id=world.round,
            listing_id=world.listing,
            widget_id=world.widget,
            quantity=quantity,
            starts_at=now - timedelta(minutes=1),
            closes_at=now + timedelta(hours=1),
            minimum_bid=minimum_bid,
        )
    ).id


def _hold_lot(world, auction_id, quantity=1):
    with Session(world.engine) as s:
        market.create_auction_lot(s, world.listing, auction_id=auction_id, quantity=quantity)
        s.commit()


def _end(world, auction_id):
    with world.engine.begin() as c:
        c.execute(
            update(Auction.__table__)
            .where(Auction.__table__.c.id == auction_id)
            .values(closes_at=datetime.now(UTC) - timedelta(seconds=1))
        )


def test_auction_needs_a_g_lot_before_opening(auctions):
    auction_id = _auction(auctions)
    assert code(auctions.runtime.auction_transition, auction_id, "open") == "CONFIGURATION_REQUIRED"
    _hold_lot(auctions, auction_id)
    assert auctions.stock() == 2  # the lot left stock
    assert auctions.runtime.auction_transition(auction_id, "open").state == "OPEN"


def test_auction_award_consumes_lot(auctions):
    auction_id = _auction(auctions)
    _hold_lot(auctions, auction_id)
    auctions.runtime.auction_transition(auction_id, "open")
    auctions.runtime.bid(auctions.team, auction_id, BidRequest(amount=50, idempotency_key=uuid4()))
    _end(auctions, auction_id)
    result = auctions.runtime.settle(auction_id)
    assert (result.winner_team_id, result.winning_amount) == (auctions.team, 50)
    assert (auctions.balance(), auctions.owned()) == (4950, 1)
    with Session(auctions.engine) as s:
        assert s.get(MarketAuctionLot, auction_id).consumed
    assert auctions.stock() == 2  # awarded units never return to stock


def test_unsold_lot_returns_to_stock(auctions):
    auction_id = _auction(auctions, quantity=2)
    _hold_lot(auctions, auction_id, quantity=2)
    auctions.runtime.auction_transition(auction_id, "open")
    assert auctions.stock() == 1
    _end(auctions, auction_id)
    assert auctions.runtime.settle(auction_id).winner_team_id is None
    assert auctions.stock() == 3


def test_paused_round_blocks_bids_but_not_settlement(auctions):
    auction_id = _auction(auctions)
    _hold_lot(auctions, auction_id)
    auctions.runtime.auction_transition(auction_id, "open")
    auctions.runtime.bid(auctions.team, auction_id, BidRequest(amount=20, idempotency_key=uuid4()))
    auctions.transition("pause")
    request = BidRequest(amount=30, idempotency_key=uuid4())
    assert code(auctions.runtime.bid, auctions.team, auction_id, request) == "MARKET_NOT_OPEN"
    auctions.transition("close")
    _end(auctions, auction_id)
    assert auctions.runtime.settle(auction_id).winning_amount == 20


def test_lot_rules(auctions):
    first = uuid4()
    _hold_lot(auctions, first)
    with Session(auctions.engine) as s:
        with pytest.raises(Exception, match="already has a lot"):
            market.create_auction_lot(s, auctions.listing, auction_id=first, quantity=1)
    with Session(auctions.engine) as s:
        with pytest.raises(Exception, match="Not enough stock"):
            market.create_auction_lot(s, auctions.listing, auction_id=uuid4(), quantity=3)
    with Session(auctions.engine) as s:
        with pytest.raises(Exception, match="finite-supply"):
            market.create_auction_lot(s, auctions.infinite, auction_id=uuid4(), quantity=1)
