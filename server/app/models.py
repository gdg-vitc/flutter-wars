"""Imports every table model so SQLModel.metadata is complete (Alembic, tests)."""

from app.auction.models import Auction, AuctionResult, Bid, BidReceipt
from app.modules.catalog.models import Widget
from app.modules.market.models import (
    Market,
    MarketAuctionLot,
    MarketListing,
    MarketRound,
    MarketRoundEvent,
)
from app.modules.pricing.models import ListingPricing, PriceHistory
from app.trading.models import TradeTransaction

# Tables whose constraints also run on SQLite (fast unit tests). I/J tables use
# PostgreSQL-only constraint syntax and are tested on PostgreSQL only.
SQLITE_SAFE = (
    Widget,
    Market,
    MarketRound,
    MarketListing,
    MarketRoundEvent,
    MarketAuctionLot,
    ListingPricing,
    PriceHistory,
)

__all__ = [
    "Auction",
    "AuctionResult",
    "Bid",
    "BidReceipt",
    "ListingPricing",
    "Market",
    "MarketAuctionLot",
    "MarketListing",
    "MarketRound",
    "MarketRoundEvent",
    "PriceHistory",
    "SQLITE_SAFE",
    "TradeTransaction",
    "Widget",
]
