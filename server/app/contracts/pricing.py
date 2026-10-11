"""Module H (pricing engine) contract, for Modules G, I and J."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar, Literal, Protocol
from uuid import UUID


@dataclass(frozen=True)
class PriceQuote:
    listing_id: UUID
    price: int
    strategy: str
    interval_index: int
    # Earliest moment the price may change; None when no repricing is scheduled.
    valid_until: datetime | None
    # The listing's stock in the same snapshot the price was computed from.
    stock_remaining: int | None


@dataclass(frozen=True)
class PricingConfig:
    listing_id: UUID
    strategy: str
    params: dict[str, Any]
    params_version: int


class PricingGateway(Protocol):
    OWNER: ClassVar[str] = "Module H pricing"

    # --- listing lifecycle (Module G, draft rounds)
    def configure_listing(
        self,
        *,
        listing_id: UUID,
        base_price: int,
        infinite_supply: bool,
        strategy: str,
        params: dict[str, Any],
    ) -> None:
        """Create or reset pricing for a listing in a draft round (validates params)."""
        ...

    def remove_listing(self, listing_id: UUID) -> None: ...
    def get_config(self, listing_id: UUID) -> PricingConfig: ...

    def get_configs(self, listing_ids: list[UUID]) -> dict[UUID, PricingConfig]:
        """Many listings' configuration in one query."""
        ...

    def on_round_opened(self, listing_ids: Iterable[UUID], opened_at: datetime) -> None:
        """Record every listing's opening price."""
        ...

    def latest_activity(self, listing_ids: list[UUID]) -> datetime | None:
        """Latest time any of these listings was traded or repriced."""
        ...

    # --- reads (Module G's listing pages)
    def quote_many(self, round_id: UUID, now: datetime) -> dict[UUID, PriceQuote]:
        """Quotes for every listing of a round, in one statement."""
        ...

    # --- trading (Module I); errors are the marketplace errors
    def get_unit_price(self, *, listing_id: UUID) -> int:
        """One whole-credit execution quote; locks the pricing row until commit."""
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
        """Count the trade towards demand AFTER the stock change, before commit."""
        ...
