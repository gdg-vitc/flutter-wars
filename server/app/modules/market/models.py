from datetime import UTC, datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

from app.contracts.market import LIVE_STATUSES, RoundKind, RoundStatus

__all__ = [
    "LIVE_STATUSES",
    "Market",
    "MarketAuctionLot",
    "MarketListing",
    "MarketRound",
    "MarketRoundEvent",
    "RoundKind",
    "RoundStatus",
]


def _now() -> datetime:
    return datetime.now(UTC)


class Market(SQLModel, table=True):
    __tablename__ = "market"
    __table_args__ = (
        # At most one active market: GET /market needs no market id.
        sa.Index(
            "uq_market_single_active",
            "is_active",
            unique=True,
            postgresql_where=sa.text("is_active"),
            sqlite_where=sa.text("is_active"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    name: str = Field(max_length=120)
    is_active: bool = True
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))


class MarketRound(SQLModel, table=True):
    __tablename__ = "market_round"
    __table_args__ = (
        sa.UniqueConstraint("market_id", "sequence", name="uq_market_round_sequence"),
        # At most one open-or-paused round per market, enforced by the database
        # so two organizers cannot open two rounds at once.
        sa.Index(
            "uq_market_round_single_live",
            "market_id",
            unique=True,
            postgresql_where=sa.text("status IN ('open', 'paused')"),
            sqlite_where=sa.text("status IN ('open', 'paused')"),
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    market_id: UUID = Field(foreign_key="market.id", index=True)
    sequence: int
    name: str = Field(max_length=120)
    kind: str = Field(sa_type=sa.String(16))
    status: str = Field(default=RoundStatus.DRAFT, sa_type=sa.String(16))
    # Informational only: lifecycle changes are organizer actions (see docs/market.md TBDs).
    scheduled_open_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    scheduled_close_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    opened_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    paused_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    closed_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    finalized_at: datetime | None = Field(default=None, sa_type=sa.DateTime(timezone=True))
    paused_seconds: float = Field(default=0, sa_column=sa.Column(sa.Float, nullable=False, server_default="0"))
    # Bumped on every transition; used for optimistic concurrency between organizers.
    version: int = 1
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))


class MarketListing(SQLModel, table=True):
    """A widget offered in one round. Infinite supply is supply_total = stock_remaining = NULL."""

    __tablename__ = "market_listing"
    __table_args__ = (
        sa.UniqueConstraint("round_id", "widget_id", name="uq_market_listing_round_widget"),
        sa.CheckConstraint("base_price > 0", name="ck_market_listing_base_price_positive"),
        sa.CheckConstraint("supply_total IS NULL OR supply_total >= 0", name="ck_market_listing_supply_nonneg"),
        sa.CheckConstraint("stock_remaining IS NULL OR stock_remaining >= 0", name="ck_market_listing_stock_nonneg"),
        sa.CheckConstraint(
            "(supply_total IS NULL) = (stock_remaining IS NULL)",
            name="ck_market_listing_infinite_consistent",
        ),
        sa.CheckConstraint(
            "max_per_purchase IS NULL OR max_per_purchase > 0",
            name="ck_market_listing_max_per_purchase",
        ),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    round_id: UUID = Field(foreign_key="market_round.id", index=True)
    widget_id: str = Field(foreign_key="widget.id", index=True, max_length=40)
    base_price: int = Field(sa_type=sa.BigInteger)
    demand_seed: int = Field(default=0, sa_column=sa.Column(sa.BigInteger, nullable=False, server_default="0"))
    supply_total: int | None = None
    stock_remaining: int | None = None
    max_per_purchase: int | None = None
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))

    @property
    def infinite_supply(self) -> bool:
        return self.supply_total is None


class MarketRoundEvent(SQLModel, table=True):
    """Append-only record of every lifecycle transition (who, when, why)."""

    __tablename__ = "market_round_event"

    id: int | None = Field(default=None, primary_key=True)
    round_id: UUID = Field(foreign_key="market_round.id", index=True)
    action: str = Field(sa_type=sa.String(16))
    from_status: str = Field(sa_type=sa.String(16))
    to_status: str = Field(sa_type=sa.String(16))
    actor: str = Field(max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))


class MarketAuctionLot(SQLModel, table=True):
    """Units of an auction-round listing held for one Auction Engine (J) auction.

    The units leave the listing's stock when the lot is created, so trading
    and other lots can never promise them twice. J consumes the lot when it
    awards the widgets; an unconsumed lot can be released back to stock.
    """

    __tablename__ = "market_auction_lot"
    __table_args__ = (sa.CheckConstraint("quantity > 0", name="ck_market_auction_lot_quantity"),)

    auction_id: UUID = Field(primary_key=True)  # J's auction id; no FK across owners
    listing_id: UUID = Field(foreign_key="market_listing.id", index=True)
    quantity: int
    consumed: bool = False
    created_at: datetime = Field(default_factory=_now, sa_type=sa.DateTime(timezone=True))
