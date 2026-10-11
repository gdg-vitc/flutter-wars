"""Module G (market rounds and listings) contract, for Modules H, I, J and K.

Module G owns the market, round, listing and auction-lot tables. Pricing (H) needs
listing facts to compute a price consistent with stock; it gets them here, either
as one locked lookup (trades) or as a read model it can join in one statement
(quotes), never by reading Module G's tables itself.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar, Literal, Protocol
from uuid import UUID


class RoundKind(StrEnum):
    TRADING = "trading"  # fixed-price purchases through the Transaction Engine (Module I)
    AUCTION = "auction"  # bidding through the Auction Engine (Module J)


class RoundStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    PAUSED = "paused"
    CLOSED = "closed"
    FINALIZED = "finalized"


LIVE_STATUSES = (RoundStatus.OPEN, RoundStatus.PAUSED)

AuctionOperation = Literal["bid", "settle", "view"]


@dataclass(frozen=True)
class ListingFacts:
    """What Pricing needs about a listing and its round, read at one instant."""

    listing_id: UUID
    round_id: UUID
    base_price: int
    infinite_supply: bool
    stock_remaining: int | None
    round_status: RoundStatus
    opened_at: datetime | None
    closed_at: datetime | None
    paused_at: datetime | None = None
    paused_seconds: float = 0
    supply_total: int | None = None
    demand_seed: int = 0


# Columns of MarketGateway.listing_facts_view(), one row per listing.
LISTING_FACTS_COLUMNS = (
    "listing_id",
    "round_id",
    "base_price",
    "infinite_supply",
    "stock_remaining",
    "round_status",
    "opened_at",
    "closed_at",
    "paused_at",
    "paused_seconds",
    "supply_total",
    "demand_seed",
)


@dataclass(frozen=True)
class PurchaseListing:
    listing_id: UUID
    round_id: UUID
    widget_id: str


class MarketGateway(Protocol):
    OWNER: ClassVar[str] = "Module G market"

    # --- organizer dashboard (Module K)
    def status_summary(self) -> dict[str, Any]: ...

    def current_round_id(self) -> UUID | None: ...

    def stream_snapshot(self, round_id: UUID, now: datetime) -> dict[str, Any]:
        """Public round state and price/stock pairs; rejects draft rounds. No writes."""
        ...

    # --- pricing (Module H)
    def require_listing(self, listing_id: UUID, *, visible_only: bool) -> None:
        """404 LISTING_NOT_FOUND unless the listing exists (and, if visible_only, is
        outside a draft round)."""
        ...

    def lock_listing_facts(self, listing_id: UUID) -> ListingFacts:
        """Share-lock the listing's round (the lock trades take first) and return its facts."""
        ...

    def listing_stock(self, listing_id: UUID) -> int | None:
        """Fresh stock read; call after taking the pricing lock."""
        ...

    def listing_facts_view(self) -> Any:
        """A SQL selectable (subquery) with LISTING_FACTS_COLUMNS, for one-statement joins."""
        ...

    # --- trading and auctions (Modules I, J); errors are the marketplace errors
    def get_for_purchase(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        """Guard round then listing; reject wrong mode/state/stock until commit."""
        ...

    def get_for_resale(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        """Guard round/listing; only finite listings take resales."""
        ...

    def consume_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None: ...
    def restore_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None: ...

    def guard_auction(
        self,
        *,
        auction_id: UUID,
        round_id: UUID,
        listing_id: UUID,
        widget_id: str,
        quantity: int,
        operation: AuctionOperation,
    ) -> None:
        """Guard lifecycle/allocation BEFORE the auction lock. Bidding needs an OPEN round;
        settlement stays possible after close; view checks identity only."""
        ...

    def consume_auction_allocation(self, *, auction_id: UUID) -> None: ...

    def release_auction_lot(self, *, auction_id: UUID) -> None:
        """Put the units of an auction nobody bid on back in stock."""
        ...
