"""Market (G) implementation of the I/J MarketPort (app.integration.contracts).

Thin translation only: the rules live in app.modules.market.service. G's
error codes are mapped to the port-level errors I/J document; anything else
(for example QUANTITY_LIMIT_EXCEEDED) passes through unchanged.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlmodel import Session

from app.core.errors import AppError
from app.integration.contracts import Adapters, PurchaseListing
from app.integration.errors import (
    BusinessError,
    ConfigurationRequired,
    InsufficientStock,
    InvalidListing,
    MarketNotOpen,
    ResaleNotAllowed,
)
from app.modules.market import service
from app.modules.market.models import RoundKind

_PORT_ERRORS: dict[str, Callable[[], BusinessError]] = {
    "LISTING_NOT_FOUND": InvalidListing,
    "WRONG_ROUND_KIND": InvalidListing,
    "ROUND_NOT_OPEN": MarketNotOpen,
    "OUT_OF_STOCK": InsufficientStock,
    "AUCTION_LOT_MISSING": ConfigurationRequired,
}


@contextmanager
def _port_errors() -> Iterator[None]:
    try:
        yield
    except BusinessError:
        raise
    except AppError as exc:
        port_error = _PORT_ERRORS.get(exc.code)
        if port_error is None:
            raise
        raise port_error() from None


class MarketAdapter:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _tradable(self, listing_id: UUID) -> service.TradableListing:
        with _port_errors():
            return service.lock_listing_for_trade(self.session, listing_id, kind=RoundKind.TRADING)

    def get_for_purchase(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        listing = self._tradable(listing_id)
        if not listing.infinite_supply and (listing.stock_remaining or 0) < quantity:
            raise InsufficientStock()
        return PurchaseListing(listing.listing_id, listing.round_id, listing.widget_id)

    def get_for_resale(self, *, listing_id: UUID, quantity: int) -> PurchaseListing:
        # Resale goes back into the same listing, and only finite listings
        # take resales (an infinite listing has no stock to return to).
        listing = self._tradable(listing_id)
        if listing.infinite_supply:
            raise ResaleNotAllowed()
        return PurchaseListing(listing.listing_id, listing.round_id, listing.widget_id)

    def consume_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None:
        with _port_errors():
            service.take_stock(self.session, listing_id, quantity)

    def restore_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None:
        with _port_errors():
            service.return_stock(self.session, listing_id, quantity)

    def guard_auction(
        self,
        *,
        auction_id: UUID,
        round_id: UUID,
        listing_id: UUID,
        widget_id: UUID,
        quantity: int,
        operation: service.AuctionOperation,
    ) -> None:
        with _port_errors():
            service.guard_auction_lot(
                self.session,
                auction_id=auction_id,
                round_id=round_id,
                listing_id=listing_id,
                widget_id=widget_id,
                quantity=quantity,
                operation=operation,
            )

    def consume_auction_allocation(self, *, auction_id: UUID) -> None:
        with _port_errors():
            service.consume_auction_lot(self.session, auction_id)


def release_unsold_lot(session: Session, auction, adapters: Adapters) -> None:
    """A NoBidHandler for J: put the units of an auction nobody bid on back in stock.

    Whether unsold lots return to stock is an organizer policy (TBD); pass
    this as BackendModules(no_bid_handler=...) once that is agreed.
    """
    with _port_errors():
        service.release_auction_lot(session, auction.id)
