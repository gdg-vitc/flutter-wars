"""Modules G-J test fixtures (and Module M infrastructure tests).

These suites create and drop their own tables, so they need their own disposable
PostgreSQL database: MODULES_TEST_DATABASE_URL, named *_modules_test (see
tests/conftest.py). Without it they are skipped.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  (registers every table)
from app.auction.models import Auction, AuctionState
from app.contracts.marketplace import Adapters
from app.contracts.principal import Principal
from app.core.clock import get_now
from app.core.db import get_session
from app.core.errors import AppError
from app.core.principal import get_principal
from app.integration.runtime import BackendModules
from app.main import create_app
from app.modules.admin import OrganizerPrincipal, Role
from app.modules.admin.authz import require_organizer
from app.modules.admin.errors import NotOrganizer
from app.modules.admin.models import AdminActionLog
from app.modules.admin.permissions import ROLE_PERMISSIONS
from app.modules.catalog.models import Widget
from app.modules.market.models import Market, MarketAuctionLot, MarketListing, MarketRound, MarketRoundEvent
from app.modules.pricing.models import ListingPricing, PriceHistory
from app.trading.models import TradeTransaction
from tests.market.adapters import (
    CatalogAdapter,
    FixedTestBrokerage,
    InventoryAdapter,
    LedgerAdapter,
    MarketAdapter,
    PricingAdapter,
    allocations,
    catalog,
    entries,
    inventory,
    listings,
    metadata,
    rounds,
    wallets,
)

PG_URL = os.environ.get("MODULES_TEST_DATABASE_URL")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
# Module D's catalog plus Modules G/H. I/J tables come from the `env` fixtures below.
GH_TABLES = (
    AdminActionLog,
    TradeTransaction,
    Auction,
    Widget,
    Market,
    MarketRound,
    MarketListing,
    MarketRoundEvent,
    MarketAuctionLot,
    ListingPricing,
    PriceHistory,
)


def _make_engine() -> Engine:
    if not PG_URL:
        pytest.skip("Set MODULES_TEST_DATABASE_URL to a disposable PostgreSQL *_modules_test database.")
    if not (make_url(PG_URL).database or "").endswith("_modules_test"):
        raise RuntimeError("Module tests only reset databases named *_modules_test.")
    return create_engine(PG_URL)


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = _make_engine()
    tables = [model.__table__ for model in GH_TABLES]
    SQLModel.metadata.drop_all(engine, tables=tables)
    SQLModel.metadata.create_all(engine, tables=tables)
    yield engine
    SQLModel.metadata.drop_all(engine, tables=tables)
    engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as session:
        yield session


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def advance(self, **kwargs: float) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


ORGANIZER = Principal(user_id="organizer", role="organizer", email="organizer@example.com")
PARTICIPANT = Principal(
    user_id="player",
    role="participant",
    email="player@example.com",
    team_id=UUID("00000000-0000-4000-8000-0000000000aa"),
)
# What Module K returns for ORGANIZER: an OPERATOR row in its organizer table.
ORGANIZER_GRANT = OrganizerPrincipal(
    id=UUID("00000000-0000-4000-8000-0000000000bb"),
    email=ORGANIZER.email,
    display_name="Organizer",
    role=Role.OPERATOR,
    permissions=ROLE_PERMISSIONS[Role.OPERATOR],
)


class Api:
    """TestClient wrapper with a switchable principal."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.principal: Principal | None = ORGANIZER

    def as_(self, principal: Principal | None) -> "Api":
        self.principal = principal
        return self

    def __getattr__(self, name: str):  # get/post/patch/delete
        return getattr(self.client, name)


@pytest.fixture
def api(engine: Engine, clock: Clock) -> Iterator[Api]:
    app = create_app()

    def _session() -> Iterator[Session]:
        with Session(engine) as session:
            yield session

    wrapper: Api

    def _principal() -> Principal:
        if wrapper.principal is None:
            raise AppError("AUTHENTICATION_REQUIRED", "Authentication is required.", 401)
        return wrapper.principal

    def _organizer(principal: Principal = Depends(get_principal)) -> OrganizerPrincipal:
        if principal.email != ORGANIZER.email:
            raise NotOrganizer()
        return ORGANIZER_GRANT

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_now] = lambda: clock.now
    app.dependency_overrides[get_principal] = _principal
    app.dependency_overrides[require_organizer] = _organizer
    with TestClient(app) as client:
        wrapper = Api(client)
        yield wrapper


@pytest.fixture
def widgets(session: Session) -> list[Widget]:
    """Module D catalog rows; the last one is archived."""
    names = ("Button", "ListView", "Card", "AnimatedContainer", "OldWidget")
    items = [
        Widget(id=name.lower(), appdev_key=f"appdev.{name.lower()}", display_name=name, category="test")
        for name in names
    ]
    items[-1].status = "ARCHIVED"
    items[-1].archived_at = T0
    session.add_all(items)
    session.commit()
    for item in items:
        session.refresh(item)
    return items


# --- Modules I/J (trading, auction): PostgreSQL-only, owner stand-in adapters ---


@pytest.fixture(scope="session")
def ij_engine():
    url = PG_URL
    if not url:
        pytest.skip("Set MODULES_TEST_DATABASE_URL to a disposable PostgreSQL *_modules_test database.")
    if not (make_url(url).database or "").endswith("_modules_test"):
        raise RuntimeError("Tests only reset databases named *_modules_test.")
    engine = create_engine(url, isolation_level="READ COMMITTED", pool_size=8, max_overflow=8)
    yield engine
    engine.dispose()


class Environment:
    def __init__(self, engine):
        self.engine = engine
        self.team, self.other_team, self.round = (uuid4() for _ in range(3))
        self.widget = f"w_{uuid4().hex[:10]}"  # Module D widget ids are string slugs
        self.listing, self.auction_listing, self.auction = (uuid4() for _ in range(3))
        self.fail_inventory = self.fail_credit = False
        with engine.begin() as c:
            c.execute(rounds.insert().values(id=self.round, state="OPEN"))
            c.execute(catalog.insert().values(id=self.widget))
            c.execute(
                wallets.insert(),
                [dict(team_id=t, balance=5000, reserved=0) for t in (self.team, self.other_team)],
            )
            c.execute(
                listings.insert(),
                [
                    dict(
                        id=self.listing,
                        round_id=self.round,
                        widget_id=self.widget,
                        mode="NORMAL",
                        finite=True,
                        stock=5,
                        price=100,
                    ),
                    dict(
                        id=self.auction_listing,
                        round_id=self.round,
                        widget_id=self.widget,
                        mode="AUCTION",
                        finite=True,
                        stock=0,
                        price=100,
                    ),
                ],
            )
            c.execute(
                allocations.insert().values(
                    auction_id=self.auction,
                    listing_id=self.auction_listing,
                    quantity=1,
                    consumed=False,
                )
            )
        with Session(engine) as s, s.begin():
            s.add(
                Auction(
                    id=self.auction,
                    round_id=self.round,
                    listing_id=self.auction_listing,
                    widget_id=self.widget,
                    quantity=1,
                    state=AuctionState.OPEN,
                    minimum_bid=1,
                    starts_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    closes_at=datetime.now(timezone.utc) + timedelta(hours=1),
                )
            )
        self.runtime = BackendModules(
            session_factory=lambda: Session(engine),
            adapter_factory=self.adapters,
            brokerage=FixedTestBrokerage(),
        )

    def adapters(self, s):
        return Adapters(
            market=MarketAdapter(s),
            pricing=PricingAdapter(s),
            ledger=LedgerAdapter(s, self),
            inventory=InventoryAdapter(s, self),
            catalog=CatalogAdapter(s),
        )

    def snapshot(self):
        with self.engine.connect() as c:
            w = c.execute(select(wallets).where(wallets.c.team_id == self.team)).mappings().one()
            stock = c.execute(select(listings.c.stock).where(listings.c.id == self.listing)).scalar_one()
            owned = (
                c.execute(
                    select(inventory.c.quantity).where(
                        inventory.c.team_id == self.team, inventory.c.widget_id == self.widget
                    )
                ).scalar_one_or_none()
                or 0
            )
            trades = c.execute(
                select(func.count())
                .select_from(TradeTransaction.__table__)
                .where(TradeTransaction.team_id == self.team)
            ).scalar_one()
            return dict(
                balance=w["balance"],
                reserved=w["reserved"],
                stock=stock,
                owned=owned,
                trades=trades,
            )

    def change(self, table, condition, **values):
        with self.engine.begin() as c:
            c.execute(update(table).where(condition).values(**values))

    def set_round(self, value):
        self.change(rounds, rounds.c.id == self.round, state=value)

    def set_balance(self, value):
        self.change(wallets, wallets.c.team_id == self.team, balance=value)

    def set_stock(self, value):
        self.change(listings, listings.c.id == self.listing, stock=value)

    def set_price(self, value):
        self.change(listings, listings.c.id == self.listing, price=value)

    def set_infinite(self):
        self.change(listings, listings.c.id == self.listing, finite=False, stock=None)

    def close_auction(self):
        self.change(
            Auction.__table__,
            Auction.id == self.auction,
            closes_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )

    def total_owned(self):
        with self.engine.connect() as c:
            return c.execute(select(func.sum(inventory.c.quantity))).scalar_one() or 0

    def other_reserved(self):
        with self.engine.connect() as c:
            return c.execute(select(wallets.c.reserved).where(wallets.c.team_id == self.other_team)).scalar_one()

    def settlement_debits(self):
        with self.engine.connect() as c:
            return c.execute(select(func.count()).select_from(entries).where(entries.c.kind == "AUCTION")).scalar_one()


@pytest.fixture
def env(ij_engine):
    engine = ij_engine
    SQLModel.metadata.drop_all(engine)
    metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    metadata.create_all(engine)
    yield Environment(engine)
    SQLModel.metadata.drop_all(engine)
    metadata.drop_all(engine)


@pytest.fixture(scope="session")
def postgres_url() -> str:
    """Module M infrastructure tests: the same disposable PostgreSQL database."""
    if not PG_URL:
        pytest.skip("Set MODULES_TEST_DATABASE_URL to run PostgreSQL infrastructure tests.")
    return PG_URL
