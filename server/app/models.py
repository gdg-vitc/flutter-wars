"""Imports every table model so SQLModel.metadata is complete (Alembic, tests)."""

from app.modules.catalog.models import Widget
from app.modules.market.models import (
    Market,
    MarketAuctionLot,
    MarketListing,
    MarketRound,
    MarketRoundEvent,
)
from app.modules.pricing.models import ListingPricing, PriceHistory

# Tables whose constraints also run on SQLite (fast unit tests).
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
    "ListingPricing",
    "Market",
    "MarketAuctionLot",
    "MarketListing",
    "MarketRound",
    "MarketRoundEvent",
    "PriceHistory",
    "SQLITE_SAFE",
    "Widget",
]
