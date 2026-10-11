"""What Trading (I) and Auction (J) use from the modules that own markets, prices,
credits, widgets and inventory: their gateways, bound to one shared session.

Each owner implements its part of these calls in its own gateway (app/contracts/
market.py, pricing.py, ledger.py, inventory.py, catalog.py). Errors raised to
Trading and Auction are the marketplace errors (app/contracts/marketplace_errors.py).
"""

from dataclasses import dataclass, fields
from typing import Protocol
from uuid import UUID

from sqlmodel import Session

from app.contracts.catalog import CatalogGateway
from app.contracts.inventory import InventoryGateway
from app.contracts.ledger import LedgerGateway
from app.contracts.market import MarketGateway, PurchaseListing
from app.contracts.pricing import PricingGateway
from app.core.services import gateway

MAX_CREDITS = 2_147_483_647

MarketPort = MarketGateway
PricingPort = PricingGateway
LedgerPort = LedgerGateway
InventoryPort = InventoryGateway
CatalogPort = CatalogGateway

__all__ = [
    "MAX_CREDITS",
    "Adapters",
    "BrokeragePolicy",
    "PurchaseListing",
    "MarketPort",
    "PricingPort",
    "LedgerPort",
    "InventoryPort",
    "CatalogPort",
]


class BrokeragePolicy(Protocol):
    def fee(
        self,
        *,
        team_id: UUID,
        listing: PurchaseListing,
        quantity: int,
        unit_price: int,
        gross_amount: int,
        prior_quantity: int = 0,
    ) -> int:
        """Policy owns fee formula AND explicit whole-credit rounding.

        prior_quantity: units this team already resold into this listing. A
        quantity-dependent policy continues from there, so splitting one sale
        into many requests cannot lower its total fee.
        """
        ...


@dataclass(frozen=True)
class Adapters:
    market: MarketGateway
    pricing: PricingGateway
    ledger: LedgerGateway
    inventory: InventoryGateway
    catalog: CatalogGateway

    @classmethod
    def resolve(cls, session: Session) -> "Adapters":
        """The registered owner gateways, all bound to `session`."""
        return cls(
            market=gateway(MarketGateway, session),
            pricing=gateway(PricingGateway, session),
            ledger=gateway(LedgerGateway, session),
            inventory=gateway(InventoryGateway, session),
            catalog=gateway(CatalogGateway, session),
        )

    def check_session(self, session: Session) -> None:
        for field in fields(self):
            if getattr(getattr(self, field.name), "session", None) is not session:
                raise ValueError("Every adapter must use the caller's shared session.")
