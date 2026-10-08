from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlmodel import Session

MAX_CREDITS = 2_147_483_647


@dataclass(frozen=True)
class PurchaseListing:
    listing_id: UUID
    round_id: UUID
    widget_id: UUID


class LedgerPort(Protocol):
    session: Session

    def lock_accounts(self, *, team_ids: Sequence[UUID]) -> None:
        """Lock account guards in sorted UUID order; every spend/reservation uses them."""
        ...

    def debit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None: ...
    def credit(self, *, team_id: UUID, amount: int, trade_id: UUID) -> None: ...
    def reserve(self, *, team_id: UUID, reservation_id: UUID, additional_amount: int) -> None: ...
    def release(self, *, team_id: UUID, reservation_id: UUID) -> None: ...
    def settle(
        self, *, team_id: UUID, reservation_id: UUID, amount: int, reference: UUID
    ) -> None: ...


class InventoryPort(Protocol):
    session: Session

    def add(self, *, team_id: UUID, widget_id: UUID, quantity: int, reference: UUID) -> None: ...
    def remove(self, *, team_id: UUID, widget_id: UUID, quantity: int, reference: UUID) -> None: ...


class MarketPort(Protocol):
    session: Session

    def get_for_purchase(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        """Guard round then listing; reject wrong mode/state/stock until commit."""
        ...

    def get_for_resale(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        """Guard round/listing; enforce owner-approved resale mapping and finite supply."""
        ...

    def consume_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None: ...
    def restore_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None: ...
    def guard_auction(
        self,
        *,
        auction_id: UUID,
        round_id: UUID,
        listing_id: UUID,
        widget_id: UUID,
        quantity: int,
        operation: Literal["bid", "settle", "view"],
    ) -> None:
        """Guard lifecycle/allocation BEFORE auction lock. Confirm an owner-reserved lot.

        Bidding requires OPEN round. Settlement remains possible after round close.
        Owner decides pause policy. View validates identity without requiring OPEN.
        """
        ...

    def consume_auction_allocation(self, *, auction_id: UUID) -> None: ...


class PricingPort(Protocol):
    session: Session

    def get_unit_price(self, *, listing_id: UUID) -> int:
        """One whole-credit execution quote under guarded listing/price state."""
        ...

    def record_trade(
        self,
        *,
        listing_id: UUID,
        transaction_type: Literal["BUY", "SELL"],
        quantity: int,
        unit_price: int,
        trade_id: UUID,
    ) -> None:
        """Apply owner-approved price/history effects AFTER stock changes, before commit.

        A static or scheduled strategy can do nothing. Never publish tentative
        cache values externally or commit independently; rollback must include these effects.
        """
        ...


class CatalogPort(Protocol):
    session: Session

    def validate_widget(
        self, *, widget_id: UUID, operation: Literal["buy", "sell", "award"]
    ) -> None:
        """Resolve identity and apply owner-configured archive/eligibility policy."""
        ...


class BrokeragePolicy(Protocol):
    def fee(
        self,
        *,
        team_id: UUID,
        listing: PurchaseListing,
        quantity: int,
        unit_price: int,
        gross_amount: int,
    ) -> int:
        """Policy owns fee formula AND explicit whole-credit rounding."""
        ...


@dataclass(frozen=True)
class Adapters:
    market: MarketPort
    pricing: PricingPort
    ledger: LedgerPort
    inventory: InventoryPort
    catalog: CatalogPort

    def check_session(self, session: Session) -> None:
        for adapter in (self.market, self.pricing, self.ledger, self.inventory, self.catalog):
            if adapter.session is not session:
                raise ValueError("Every adapter must use the caller's shared session.")
