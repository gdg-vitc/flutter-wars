from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.market.models import RoundKind, RoundStatus

MAX_PRICE = 1_000_000_000
MAX_SUPPLY = 1_000_000

SupplyIn = Annotated[int, Field(ge=0, le=MAX_SUPPLY)] | Literal["infinite"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- requests ---


class MarketCreate(_In):
    name: str = Field(min_length=1, max_length=120)


class PricingConfigIn(_In):
    strategy: str = Field(default="static", max_length=40)
    params: dict[str, Any] = Field(default_factory=dict)


class ListingCreate(_In):
    widget_id: UUID
    base_price: int = Field(ge=1, le=MAX_PRICE)
    supply: SupplyIn = Field(description='Units available, or "infinite".')
    max_per_purchase: int | None = Field(default=None, ge=1, le=MAX_SUPPLY)
    pricing: PricingConfigIn = Field(default_factory=PricingConfigIn)


class ListingUpdate(_In):
    """Only the fields present in the request are changed."""

    base_price: int | None = Field(default=None, ge=1, le=MAX_PRICE)
    supply: SupplyIn | None = None
    max_per_purchase: int | None = Field(default=None, ge=1, le=MAX_SUPPLY)
    pricing: PricingConfigIn | None = None


class RoundCreate(_In):
    name: str = Field(min_length=1, max_length=120)
    kind: RoundKind
    scheduled_open_at: datetime | None = None
    scheduled_close_at: datetime | None = None
    listings: list[ListingCreate] = Field(default_factory=list, max_length=500)


class AuctionLotCreate(_In):
    auction_id: UUID = Field(description="The Auction Engine's id for the auction (DRAFT).")
    quantity: int = Field(ge=1, le=MAX_SUPPLY)


class TransitionIn(_In):
    expected_version: int | None = Field(
        default=None, description="Reject if the round changed since this version."
    )
    reason: str | None = Field(default=None, max_length=500)


# --- responses ---


class MarketOut(BaseModel):
    id: UUID
    name: str
    is_active: bool
    created_at: datetime


class RoundOut(BaseModel):
    id: UUID
    sequence: int
    name: str
    kind: RoundKind
    status: RoundStatus
    scheduled_open_at: datetime | None
    scheduled_close_at: datetime | None
    opened_at: datetime | None
    paused_at: datetime | None
    closed_at: datetime | None
    finalized_at: datetime | None


class RoundAdminOut(RoundOut):
    market_id: UUID
    version: int
    created_at: datetime


class PriceOut(BaseModel):
    amount: int
    strategy: str
    interval_index: int
    valid_until: datetime | None = Field(
        description="Earliest time the price may change; null if no change is scheduled."
    )


class ListingOut(BaseModel):
    id: UUID
    round_id: UUID
    widget_id: UUID
    widget_name: str | None
    base_price: int
    infinite_supply: bool
    supply_total: int | None
    stock_remaining: int | None
    sold_out: bool
    max_per_purchase: int | None
    price: PriceOut | None


class PricingConfigOut(BaseModel):
    strategy: str
    params: dict[str, Any]
    params_version: int


class ListingAdminOut(ListingOut):
    pricing: PricingConfigOut


class RoundDetailOut(RoundAdminOut):
    listings: list[ListingAdminOut]


class MarketSummary(BaseModel):
    market: MarketOut | None
    current_round: RoundOut | None
    server_time: datetime


class ListingsOut(BaseModel):
    round: RoundOut | None
    listings: list[ListingOut]
    server_time: datetime


class RoundEventOut(BaseModel):
    id: int
    action: str
    from_status: RoundStatus
    to_status: RoundStatus
    actor: str
    reason: str | None
    created_at: datetime


class AuctionLotOut(BaseModel):
    auction_id: UUID
    listing_id: UUID
    quantity: int
    consumed: bool
    created_at: datetime
