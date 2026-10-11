"""Imports every table model so SQLModel.metadata is complete (Alembic, tests).

One line per owning module; a new table must be registered here.
"""

from app.auction.models import Auction, AuctionResult, Bid, BidReceipt  # J
from app.modules.admin.models import AdminActionLog, OperationalControl, Organizer  # K
from app.modules.authentication.model import Team, TeamMembership, UserIdentity  # B
from app.modules.authentication.sessions import (  # B
    AuthRateBucket,
    AuthSession,
    LoginCode,
    OAuthAttempt,
    RefreshCredential,
)
from app.modules.catalog.models import Widget  # D
from app.modules.ide_sync.model import TeamApiKey  # C
from app.modules.inventory.models import InventoryEvent, TeamWidgetInventory  # F
from app.modules.ledger.models import CreditLedgerEntry, CreditReservation, TeamWallet  # E
from app.modules.market.models import (  # G
    Market,
    MarketAuctionLot,
    MarketListing,
    MarketRound,
    MarketRoundEvent,
)
from app.modules.pricing.models import ListingPricing, PriceHistory  # H
from app.trading.models import ResaleAccount, ResalePosition, TradeTransaction  # I

__all__ = [
    "AuthRateBucket",
    "AuthSession",
    "LoginCode",
    "OAuthAttempt",
    "RefreshCredential",
    "AdminActionLog",
    "Auction",
    "AuctionResult",
    "Bid",
    "BidReceipt",
    "CreditLedgerEntry",
    "CreditReservation",
    "InventoryEvent",
    "ListingPricing",
    "Market",
    "MarketAuctionLot",
    "MarketListing",
    "MarketRound",
    "MarketRoundEvent",
    "OperationalControl",
    "Organizer",
    "PriceHistory",
    "Team",
    "TeamApiKey",
    "TeamMembership",
    "TeamWallet",
    "TeamWidgetInventory",
    "TradeTransaction",
    "ResaleAccount",
    "ResalePosition",
    "UserIdentity",
    "Widget",
]
