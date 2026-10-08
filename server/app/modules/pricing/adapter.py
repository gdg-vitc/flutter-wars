"""Pricing (H) implementation of the I/J PricingPort (app.integration.contracts).

The price is computed and the pricing row locked inside the caller's
transaction, so a purchase is charged the price it was quoted and its demand
is counted in the same commit (or rolled back with it). record_trade reuses the
quote's instant: reading the clock again could cross a repricing boundary and
book the trade's demand into the wrong interval.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlmodel import Session

from app.core.errors import AppError
from app.integration.errors import ConfigurationRequired, InvalidListing, InvalidPrice
from app.modules.pricing import service
from app.modules.pricing.service import TradeSide


def _utc_now() -> datetime:
    return datetime.now(UTC)


class PricingAdapter:
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
