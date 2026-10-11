"""Pricing (H) implementation of the I/J PricingPort (app.contracts.marketplace).

The price is computed and the pricing row locked inside the caller's
transaction, so a purchase is charged the price it was quoted and its demand
is counted in the same commit (or rolled back with it). record_trade reuses the
quote's instant: reading the clock again could cross a repricing boundary and
book the trade's demand into the wrong interval.
"""

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlmodel import Session

from app.contracts.marketplace_errors import ConfigurationRequired, InvalidListing, InvalidPrice
from app.contracts.pricing import PriceQuote, PricingConfig
from app.core.errors import AppError
from app.modules.pricing import service
from app.modules.pricing.models import ListingPricing
from app.modules.pricing.service import TradeSide


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PricingGatewayImpl:
    def __init__(self, session: Session, now: Callable[[], datetime] = _utc_now) -> None:
        self.session = session
        self._now = now
        self._quoted_at: dict[UUID, datetime] = {}

    def get_unit_price(self, *, listing_id: UUID) -> int:
        now = self._now()
        try:
            quote = service.get_current_price(self.session, listing_id, now)
        except AppError as exc:
            if exc.status_code == 404:
                raise InvalidListing() from None
            raise
        if type(quote.price) is not int or quote.price < 1:
            raise InvalidPrice()
        self._quoted_at[listing_id] = now
        return quote.price

    def record_trade(
        self,
        *,
        listing_id: UUID,
        transaction_type: Literal["BUY", "SELL"],
        quantity: int,
        unit_price: int,
        trade_id: UUID,
    ) -> None:
        quoted_at = self._quoted_at.pop(listing_id, None)
        if quoted_at is None:
            raise ConfigurationRequired("record_trade needs a quote from this transaction.")
        side = TradeSide.BUY if transaction_type == "BUY" else TradeSide.SELL
        service.record_trade(self.session, listing_id, quantity, side, quoted_at)

    def configure_listing(
        self,
        *,
        listing_id: UUID,
        base_price: int,
        infinite_supply: bool,
        strategy: str,
        params: dict[str, Any],
    ) -> None:
        service.configure_listing(
            self.session,
            listing_id=listing_id,
            base_price=base_price,
            infinite_supply=infinite_supply,
            strategy_key=strategy,
            params=params,
        )

    def remove_listing(self, listing_id: UUID) -> None:
        service.remove_listing(self.session, listing_id)

    @staticmethod
    def _config(state: ListingPricing) -> PricingConfig:
        return PricingConfig(state.listing_id, state.strategy, state.params, state.params_version)

    def get_config(self, listing_id: UUID) -> PricingConfig:
        return self._config(service.get_config(self.session, listing_id))

    def get_configs(self, listing_ids: list[UUID]) -> dict[UUID, PricingConfig]:
        return {key: self._config(state) for key, state in service.get_configs(self.session, listing_ids).items()}

    def on_round_opened(self, listing_ids: Iterable[UUID], opened_at: datetime) -> None:
        service.on_round_opened(self.session, listing_ids, opened_at)

    def latest_activity(self, listing_ids: list[UUID]) -> datetime | None:
        return service.latest_activity(self.session, listing_ids)

    def quote_many(self, round_id: UUID, now: datetime) -> dict[UUID, PriceQuote]:
        return service.quote_many(self.session, round_id, now)
