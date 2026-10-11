"""Module G's gateway (app/contracts/market.py).

Thin translation only: the rules live in app.modules.market.service. For Trading
and Auction, G's error codes are mapped to the marketplace errors they document;
anything else (for example QUANTITY_LIMIT_EXCEEDED) passes through unchanged.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, select

from app.contracts.market import AuctionOperation, ListingFacts, PurchaseListing, RoundKind, RoundStatus
from app.contracts.marketplace_errors import (
    ConfigurationRequired,
    InsufficientStock,
    InvalidListing,
    MarketNotOpen,
    ResaleNotAllowed,
    port_errors,
)
from app.contracts.pricing import PricingGateway
from app.core.errors import NotFound
from app.core.read_cache import market_reads
from app.core.services import gateway
from app.modules.market import service
from app.modules.market.models import MarketListing, MarketRound

_PORT_ERRORS = {
    "LISTING_NOT_FOUND": InvalidListing,
    "WRONG_ROUND_KIND": InvalidListing,
    "ROUND_NOT_OPEN": MarketNotOpen,
    "OUT_OF_STOCK": InsufficientStock,
    "AUCTION_LOT_MISSING": ConfigurationRequired,
}


def listing_facts_view() -> Any:
    """Listing + round facts as one subquery (see LISTING_FACTS_COLUMNS)."""
    return (
        select(
            col(MarketListing.id).label("listing_id"),
            col(MarketListing.round_id).label("round_id"),
            col(MarketListing.base_price).label("base_price"),
            col(MarketListing.supply_total).is_(None).label("infinite_supply"),
            col(MarketListing.stock_remaining).label("stock_remaining"),
            col(MarketRound.status).label("round_status"),
            col(MarketRound.opened_at).label("opened_at"),
            col(MarketRound.closed_at).label("closed_at"),
            col(MarketRound.paused_at).label("paused_at"),
            col(MarketRound.paused_seconds).label("paused_seconds"),
            col(MarketListing.supply_total).label("supply_total"),
            col(MarketListing.demand_seed).label("demand_seed"),
        )
        .join(MarketRound, col(MarketRound.id) == MarketListing.round_id)
        .subquery("listing_facts")
    )


class MarketGatewayImpl:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- organizer dashboard (Module K)

    def status_summary(self) -> dict[str, Any]:
        return service.status_summary(self.session)

    def current_round_id(self) -> UUID | None:
        rnd = service.current_round(self.session)
        return rnd.id if rnd else None

    def stream_snapshot(self, round_id: UUID, now: datetime) -> dict[str, Any]:
        def load() -> dict[str, Any]:
            rnd = service.get_round(self.session, round_id)
            if rnd.status == RoundStatus.DRAFT:
                raise NotFound("ROUND_NOT_FOUND", "Round not found.", round_id=round_id)
            quotes = gateway(PricingGateway, self.session).quote_many(round_id, now)
            deadlines = [q.valid_until for q in quotes.values() if q.valid_until is not None]
            return {
                "round_id": str(round_id),
                "status": rnd.status,
                "version": rnd.version,
                "server_time": now.isoformat(),
                "valid_until": min(deadlines).isoformat() if deadlines else None,
                "listings": [
                    {
                        "listing_id": str(q.listing_id),
                        "price": q.price,
                        "stock_remaining": q.stock_remaining,
                        "interval_index": q.interval_index,
                        "valid_until": q.valid_until.isoformat() if q.valid_until else None,
                    }
                    for q in sorted(quotes.values(), key=lambda q: str(q.listing_id))
                ],
            }

        result = market_reads.read(
            self.session,
            ("round-stream", round_id),
            now,
            load,
            deadlines=lambda payload: (
                (datetime.fromisoformat(payload["valid_until"]),) if payload["valid_until"] else ()
            ),
        )
        return result | {"server_time": now.isoformat()}

    # --- pricing (Module H)

    def require_listing(self, listing_id: UUID, *, visible_only: bool) -> None:
        if visible_only:
            service.get_visible_listing(self.session, listing_id)
        else:
            service.get_listing(self.session, listing_id)

    def lock_listing_facts(self, listing_id: UUID) -> ListingFacts:
        listing = service.get_listing(self.session, listing_id)
        rnd = service.lock_round(self.session, listing.round_id)
        return ListingFacts(
            listing_id=listing.id,
            round_id=rnd.id,
            base_price=listing.base_price,
            infinite_supply=listing.infinite_supply,
            stock_remaining=listing.stock_remaining,
            round_status=RoundStatus(rnd.status),
            opened_at=rnd.opened_at,
            closed_at=rnd.closed_at,
            paused_at=rnd.paused_at,
            paused_seconds=rnd.paused_seconds,
            supply_total=listing.supply_total,
            demand_seed=listing.demand_seed,
        )

    def listing_stock(self, listing_id: UUID) -> int | None:
        return self.session.exec(select(MarketListing.stock_remaining).where(MarketListing.id == listing_id)).one()

    def listing_facts_view(self) -> Any:
        return listing_facts_view()

    # --- trading and auctions (Modules I, J)

    def _tradable(self, listing_id: UUID) -> service.TradableListing:
        with port_errors(_PORT_ERRORS):
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
        with port_errors(_PORT_ERRORS):
            service.take_stock(self.session, listing_id, quantity)

    def restore_stock(self, *, listing_id: UUID, quantity: int, trade_id: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            service.return_stock(self.session, listing_id, quantity)

    def guard_auction(
        self,
        *,
        auction_id: UUID,
        round_id: UUID,
        listing_id: UUID,
        widget_id: str,
        quantity: int,
        operation: AuctionOperation,
    ) -> None:
        with port_errors(_PORT_ERRORS):
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
        with port_errors(_PORT_ERRORS):
            service.consume_auction_lot(self.session, auction_id)

    def release_auction_lot(self, *, auction_id: UUID) -> None:
        with port_errors(_PORT_ERRORS):
            service.release_auction_lot(self.session, auction_id, internal=True)
