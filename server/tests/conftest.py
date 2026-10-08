"""Test fixtures.

Runs against in-memory SQLite by default. Set TEST_DATABASE_URL to a
PostgreSQL URL (e.g. a throwaway Neon branch or local container) to run the
same suite plus the PostgreSQL-only concurrency tests.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import app.models  # noqa: F401  (registers every table)
from app.auction.models import Auction, AuctionState
from app.core.auth import Principal, Role, get_principal
from app.core.clock import get_now
from app.core.db import get_session
from app.integration.contracts import Adapters
from app.integration.runtime import BackendModules
from app.main import create_app
from app.modules.catalog.models import Widget
from app.trading.models import TradeTransaction
from tests.adapters import (
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

PG_URL = os.environ.get("TEST_DATABASE_URL")
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)


def _make_engine() -> Engine:
    if PG_URL:
        return create_engine(PG_URL)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_conn, _):  # type: ignore[no-untyped-def]
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return engine


@pytest.fixture
def engine() -> Iterator[Engine]:
    engine = _make_engine()
    # I/J tables are PostgreSQL-only; their own `ij_engine`/`env` fixtures create them.
    tables = [model.__table__ for model in app.models.SQLITE_SAFE]
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


ORGANIZER = Principal(subject="organizer@example.com", role=Role.ORGANIZER)
PARTICIPANT = Principal(
    subject="player@example.com",
    role=Role.PARTICIPANT,
    team_id=UUID("00000000-0000-4000-8000-0000000000aa"),
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
            return get_principal()  # the placeholder: always 401
        return wrapper.principal

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_now] = lambda: clock.now
    app.dependency_overrides[get_principal] = _principal
    with TestClient(app) as client:
        wrapper = Api(client)
        yield wrapper


@pytest.fixture
def widgets(session: Session) -> list[Widget]:
    items = [Widget(name=name) for name in ("Button", "ListView", "Card", "AnimatedContainer")]
    items.append(Widget(name="OldWidget", archived=True))
    session.add_all(items)
    session.commit()
    for item in items:
        session.refresh(item)
    return items


# --- Modules I/J (trading, auction): PostgreSQL-only, owner stand-in adapters ---


@pytest.fixture(scope="session")
def ij_engine():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL *_modules_test database.")
    if not (make_url(url).database or "").endswith("_modules_test"):
        raise RuntimeError("Tests only reset databases named *_modules_test.")
    engine = create_engine(url, isolation_level="READ COMMITTED", pool_size=8, max_overflow=8)
    yield engine
    engine.dispose()


class Environment:
    def __init__(self, engine):
        self.engine = engine
        self.team, self.other_team, self.round, self.widget = (uuid4() for _ in range(4))
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
            stock = c.execute(
                select(listings.c.stock).where(listings.c.id == self.listing)
            ).scalar_one()
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
            return c.execute(
                select(wallets.c.reserved).where(wallets.c.team_id == self.other_team)
            ).scalar_one()

    def settlement_debits(self):
        with self.engine.connect() as c:
            return c.execute(
                select(func.count()).select_from(entries).where(entries.c.kind == "AUCTION")
            ).scalar_one()


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
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL infrastructure tests.")
    return PG_URL
