"""Compatibility exports for the shared owner-facing error contract."""

from app.contracts.marketplace_errors import (
    AmountTooLarge,
    AuctionNotOpen,
    BidNotIncreasing,
    BusinessError,
    ConfigurationRequired,
    IdempotencyConflict,
    InsufficientCredits,
    InsufficientInventory,
    InsufficientStock,
    InvalidListing,
    InvalidPrice,
    MarketNotOpen,
    ResaleNotAllowed,
)

TradingError = BusinessError
__all__ = [
    "TradingError",
    "AmountTooLarge",
    "AuctionNotOpen",
    "BidNotIncreasing",
    "ConfigurationRequired",
    "IdempotencyConflict",
    "InsufficientCredits",
    "InsufficientInventory",
    "InsufficientStock",
    "InvalidListing",
    "InvalidPrice",
    "MarketNotOpen",
    "ResaleNotAllowed",
]
