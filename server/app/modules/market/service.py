"""Market & Round Lifecycle service (Module G).

Round state machine (organizer actions):

    draft --open--> open --pause--> paused --open--> open
    open/paused --close--> closed --finalize--> finalized

Transitions use optimistic concurrency on market_round.version, and the
database allows at most one open-or-paused round per market.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select, update

from app.core.errors import AppError, Conflict, NotFound
from app.modules.catalog.service import get_widget
from app.modules.market import repository as repo
from app.modules.market.models import (
    LIVE_STATUSES,
    Market,
    MarketAuctionLot,
    MarketListing,
    MarketRound,
    MarketRoundEvent,
    RoundKind,
    RoundStatus,
)
from app.modules.pricing import service as pricing

Supply = int | Literal["infinite"]
Action = Literal["open", "pause", "close", "finalize"]

TRANSITIONS: dict[Action, tuple[frozenset[RoundStatus], RoundStatus]] = {
    "open": (frozenset({RoundStatus.DRAFT, RoundStatus.PAUSED}), RoundStatus.OPEN),
    "pause": (frozenset({RoundStatus.OPEN}), RoundStatus.PAUSED),
    "close": (frozenset({RoundStatus.OPEN, RoundStatus.PAUSED}), RoundStatus.CLOSED),
    "finalize": (frozenset({RoundStatus.CLOSED}), RoundStatus.FINALIZED),
}


@dataclass(frozen=True)
class ListingSpec:
    widget_id: UUID
    base_price: int
    supply: Supply
    max_per_purchase: int | None = None
    pricing_strategy: str = "static"
    pricing_params: dict[str, Any] | None = None


# --- markets ---


def create_market(session: Session, name: str) -> Market:
    if repo.active_market(session) is not None:
        raise Conflict("MARKET_ALREADY_ACTIVE", "An active market already exists.")
    market = Market(name=name)
    session.add(market)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict("MARKET_ALREADY_ACTIVE", "An active market already exists.") from None
    return market


def require_active_market(session: Session) -> Market:
    market = repo.active_market(session)
    if market is None:
        raise NotFound("NO_ACTIVE_MARKET", "No market has been configured.")
    return market


# --- rounds ---


def create_round(
    session: Session,
    *,
    name: str,
    kind: RoundKind,
    listings: list[ListingSpec],
    scheduled_open_at: datetime | None = None,
    scheduled_close_at: datetime | None = None,
) -> MarketRound:
    market = require_active_market(session)
    if scheduled_open_at and scheduled_close_at and scheduled_close_at <= scheduled_open_at:
        raise AppError("INVALID_SCHEDULE", "scheduled_close_at must be after scheduled_open_at.")
    sequence = (
        session.exec(
            select(func.max(MarketRound.sequence)).where(MarketRound.market_id == market.id),
        ).one()
        or 0
    ) + 1
    rnd = MarketRound(
        market_id=market.id,
        sequence=sequence,
        name=name,
        kind=kind,
        scheduled_open_at=scheduled_open_at,
        scheduled_close_at=scheduled_close_at,
    )
    session.add(rnd)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict(
            "ROUND_CREATE_CONFLICT", "Another round was created at the same time; retry."
        ) from None
    for spec in listings:
        add_listing(session, rnd.id, spec)
    return rnd


def get_round(session: Session, round_id: UUID) -> MarketRound:
    rnd = session.get(MarketRound, round_id)
    if rnd is None:
        raise NotFound("ROUND_NOT_FOUND", "Round not found.", round_id=round_id)
    return rnd


def lock_round(session: Session, round_id: UUID, *, exclusive: bool = False) -> MarketRound:
    """Lock the round row (FOR SHARE, or FOR UPDATE if exclusive) and return
    it freshly read, never a stale copy from the session's identity map.

    Lock order for everything touching a round: round -> listing_pricing ->
    market_listing -> other modules' rows. Trades and live pricing edits take
    the shared lock; lifecycle transitions and draft edits take the exclusive
    one, so draft edits are serialised with each other and with opening."""
    rnd = session.exec(
        select(MarketRound)
        .where(MarketRound.id == round_id)
        .with_for_update(read=not exclusive)
        .execution_options(populate_existing=True),
    ).one_or_none()
    if rnd is None:
        raise NotFound("ROUND_NOT_FOUND", "Round not found.", round_id=round_id)
    return rnd


def current_round(session: Session) -> MarketRound | None:
    """The round participants should see: the live one, else the latest one that ran."""
    market = repo.active_market(session)
    if market is None:
        return None
    return repo.live_round(session, market.id) or repo.latest_started_round(session, market.id)


def transition(
    session: Session,
    round_id: UUID,
    action: Action,
    *,
    now: datetime,
    actor: str,
    reason: str | None = None,
    expected_version: int | None = None,
) -> MarketRound:
    # Waits for in-flight trades and draft edits (which hold the row FOR SHARE),
    # so every check below sees their committed effects.
    rnd = lock_round(session, round_id, exclusive=True)
    # The UPDATE below synchronises `rnd` in the session, so keep the old values.
    from_status, from_version = RoundStatus(rnd.status), rnd.version
    # A stale view is reported as such, even if the action is also invalid now.
    if expected_version is not None and expected_version != from_version:
        raise Conflict(
            "ROUND_VERSION_CONFLICT",
            "The round was changed by someone else.",
            version=from_version,
            status=from_status,
        )
    allowed, target = TRANSITIONS[action]
    if from_status not in allowed:
        raise Conflict(
            "INVALID_ROUND_TRANSITION",
            f"Cannot {action} a round that is {from_status}.",
            action=action,
            status=from_status,
        )

    first_open = action == "open" and from_status == RoundStatus.DRAFT
    listing_ids = repo.listing_ids(session, rnd.id)
    if target in LIVE_STATUSES and from_status not in LIVE_STATUSES:
        live = repo.live_round(session, rnd.market_id)
        if live is not None:
            raise Conflict(
                "ANOTHER_ROUND_LIVE",
                "Another round in this market is already open or paused.",
                round_id=live.id,
            )
    if first_open:
        if not listing_ids:
            raise Conflict(
                "ROUND_HAS_NO_LISTINGS", "A round needs at least one listing before it can open."
            )
        archived = repo.archived_widget_ids(session, rnd.id)
        if archived:
            raise Conflict(
                "ROUND_HAS_ARCHIVED_WIDGETS",
                "Remove archived widgets before opening.",
                widget_ids=archived,
            )

    values: dict[str, Any] = {"status": target, "version": from_version + 1}
    if first_open:
        values["opened_at"] = now
    if action == "pause":
        values["paused_at"] = now
    elif action == "open":
        values["paused_at"] = None
    elif action == "close":
        # A trade that committed while we waited for the lock may carry a later
        # timestamp than this request; the close must not predate it.
        latest_trade = pricing.latest_activity(session, listing_ids)
        values["closed_at"] = max(now, latest_trade) if latest_trade else now
    elif action == "finalize":
        values["finalized_at"] = now

    statement = (
        update(MarketRound)
        .where(col(MarketRound.id) == rnd.id, col(MarketRound.version) == from_version)
        .values(**values)
    )
    try:
        result = session.exec(statement)  # type: ignore[call-overload]
    except IntegrityError:
        # Lost a race against another organizer opening a different round.
        raise Conflict(
            "ANOTHER_ROUND_LIVE", "Another round in this market is already open or paused."
        ) from None
    if result.rowcount != 1:
        raise Conflict(
            "ROUND_VERSION_CONFLICT", "The round was changed by someone else; reload and retry."
        )

    session.add(
        MarketRoundEvent(
            round_id=rnd.id,
            action=action,
            from_status=from_status,
            to_status=target,
            actor=actor,
            reason=reason,
        )
    )
    if first_open:
        pricing.on_round_opened(session, listing_ids, now)
    session.flush()
    session.refresh(rnd)
    return rnd


def round_events(session: Session, round_id: UUID) -> list[MarketRoundEvent]:
    get_round(session, round_id)
    return list(
        session.exec(
            select(MarketRoundEvent)
            .where(MarketRoundEvent.round_id == round_id)
            .order_by(col(MarketRoundEvent.id)),
        )
    )


# --- listings (editable only while the round is a draft) ---


def _require_draft(rnd: MarketRound) -> None:
    if rnd.status != RoundStatus.DRAFT:
        raise Conflict(
            "ROUND_NOT_EDITABLE",
            "Listings can only be changed while the round is a draft.",
            status=rnd.status,
        )


def _supply_columns(supply: Supply) -> tuple[int | None, int | None]:
    if supply == "infinite":
        return None, None
    if supply < 0:
        raise AppError("INVALID_SUPPLY", 'Supply must be zero or more, or "infinite".')
    return supply, supply


def _check_widget(session: Session, widget_id: UUID) -> None:
    widget = get_widget(session, widget_id)
    if widget is None:
        raise NotFound("WIDGET_NOT_FOUND", "Widget not found.", widget_id=widget_id)
    if widget.archived:
        raise Conflict("WIDGET_ARCHIVED", "Archived widgets cannot be listed.", widget_id=widget_id)


def _fresh_listing(session: Session, listing_id: UUID) -> MarketListing:
    """Re-read a listing after taking a round lock; it may have been deleted meanwhile."""
    listing = session.exec(
        select(MarketListing)
        .where(MarketListing.id == listing_id)
        .execution_options(populate_existing=True),
    ).one_or_none()
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return listing


def _lock_draft_listing(session: Session, listing_id: UUID) -> MarketListing:
    _require_draft(lock_round(session, get_listing(session, listing_id).round_id, exclusive=True))
    return _fresh_listing(session, listing_id)


def add_listing(session: Session, round_id: UUID, spec: ListingSpec) -> MarketListing:
    rnd = lock_round(session, round_id, exclusive=True)
    _require_draft(rnd)
    _check_widget(session, spec.widget_id)
    if spec.base_price < 1:
        raise AppError("INVALID_PRICE", "base_price must be at least 1.")
    if repo.listing_for_widget(session, rnd.id, spec.widget_id) is not None:
        raise Conflict(
            "DUPLICATE_LISTING",
            "This widget is already listed in the round.",
            widget_id=spec.widget_id,
        )
    supply_total, stock = _supply_columns(spec.supply)
    listing = MarketListing(
        round_id=rnd.id,
        widget_id=spec.widget_id,
        base_price=spec.base_price,
        supply_total=supply_total,
        stock_remaining=stock,
        max_per_purchase=spec.max_per_purchase,
    )
    session.add(listing)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict(
            "DUPLICATE_LISTING",
            "This widget is already listed in the round.",
            widget_id=spec.widget_id,
        ) from None
    pricing.configure_listing(session, listing, spec.pricing_strategy, spec.pricing_params or {})
    return listing


_UNSET: Any = object()


def update_listing(
    session: Session,
    listing_id: UUID,
    *,
    base_price: int = _UNSET,
    supply: Supply = _UNSET,
    max_per_purchase: int | None = _UNSET,
    pricing_strategy: str = _UNSET,
    pricing_params: dict[str, Any] = _UNSET,
) -> MarketListing:
    listing = _lock_draft_listing(session, listing_id)
    if base_price is not _UNSET:
        if base_price < 1:
            raise AppError("INVALID_PRICE", "base_price must be at least 1.")
        listing.base_price = base_price
    if supply is not _UNSET:
        listing.supply_total, listing.stock_remaining = _supply_columns(supply)
    if max_per_purchase is not _UNSET:
        listing.max_per_purchase = max_per_purchase
    session.add(listing)
    current = pricing.get_config(session, listing.id)
    strategy, params = current.strategy, current.params
    if pricing_strategy is not _UNSET and pricing_strategy != strategy:
        strategy, params = pricing_strategy, {}  # old parameters belong to the old strategy
    if pricing_params is not _UNSET:
        params = pricing_params
    pricing.configure_listing(session, listing, strategy, params)
    return listing


def delete_listing(session: Session, listing_id: UUID) -> None:
    listing = _lock_draft_listing(session, listing_id)
    pricing.remove_listing(session, listing.id)
    session.flush()  # no ORM relationship, so order the deletes by hand
    session.delete(listing)


def get_listing(session: Session, listing_id: UUID) -> MarketListing:
    listing = session.get(MarketListing, listing_id)
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return listing


def get_visible_listing(session: Session, listing_id: UUID) -> MarketListing:
    """A listing participants may see: anything outside draft rounds."""
    listing = session.get(MarketListing, listing_id)
    if listing is not None and get_round(session, listing.round_id).status != RoundStatus.DRAFT:
        return listing
    raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)


def listings_for_round(session: Session, round_id: UUID) -> list[MarketListing]:
    return repo.listings(session, round_id)


# --- internal contract for Transaction (I) and Auction (J) ---


@dataclass(frozen=True)
class TradableListing:
    listing_id: UUID
    round_id: UUID
    widget_id: UUID
    round_kind: RoundKind
    base_price: int
    infinite_supply: bool
    stock_remaining: int | None
    max_per_purchase: int | None


def lock_listing_for_trade(
    session: Session, listing_id: UUID, *, kind: RoundKind
) -> TradableListing:
    """Check, inside the caller's transaction, that a listing can be traded
    right now. Holds a shared lock on the round row until the transaction
    ends, so the round cannot be paused/closed under an in-flight trade.
    Call this first, before any pricing/ledger/inventory work."""
    listing = session.get(MarketListing, listing_id)
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    rnd = lock_round(session, listing.round_id)
    if rnd.status == RoundStatus.DRAFT:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    if rnd.status != RoundStatus.OPEN:
        raise Conflict("ROUND_NOT_OPEN", "Trading is not open for this round.", status=rnd.status)
    # Re-read under the lock (listings are frozen once open). Stock is a
    # snapshot only; take_stock is the authoritative check.
    listing = _fresh_listing(session, listing_id)
    if rnd.kind != kind:
        raise Conflict(
            "WRONG_ROUND_KIND", f"This listing belongs to a {rnd.kind} round.", kind=rnd.kind
        )
    return TradableListing(
        listing.id,
        rnd.id,
        listing.widget_id,
        RoundKind(rnd.kind),
        listing.base_price,
        listing.infinite_supply,
        listing.stock_remaining,
        listing.max_per_purchase,
    )


def take_stock(session: Session, listing_id: UUID, quantity: int) -> int | None:
    """Atomically remove `quantity` units. Returns the remaining stock (None if infinite)."""
    listing = get_listing(session, listing_id)
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    if listing.max_per_purchase is not None and quantity > listing.max_per_purchase:
        raise AppError(
            "QUANTITY_LIMIT_EXCEEDED",
            "Quantity is above the per-purchase limit.",
            max_per_purchase=listing.max_per_purchase,
        )
    stock = col(MarketListing.stock_remaining)
    result = session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == listing_id, sa.or_(stock.is_(None), stock >= quantity))
        .values(stock_remaining=stock - quantity)
        .returning(stock),
    ).one_or_none()
    if result is None:
        session.refresh(listing)
        raise Conflict("OUT_OF_STOCK", "Not enough stock left.", available=listing.stock_remaining)
    session.expire(listing)
    return result[0]


def return_stock(session: Session, listing_id: UUID, quantity: int) -> int | None:
    """Put units back (resale). Infinite listings stay infinite."""
    get_listing(session, listing_id)
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    stock = col(MarketListing.stock_remaining)
    result = session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == listing_id)
        .values(stock_remaining=stock + quantity)
        .returning(stock),
    ).one()
    session.expire_all()
    return result[0]


# --- auction lots: the market side of an Auction Engine (J) auction ---

AuctionOperation = Literal["bid", "settle", "view"]


def create_auction_lot(
    session: Session, listing_id: UUID, *, auction_id: UUID, quantity: int
) -> MarketAuctionLot:
    """Hold `quantity` units of an auction-round listing for one J auction.

    Organizer action, after J has created the auction (DRAFT) and before J
    opens it. Allowed once the round has opened (listings are frozen then),
    until it closes. The units leave stock now, so nothing can sell them twice.
    """
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    listing = get_listing(session, listing_id)
    rnd = lock_round(session, listing.round_id)
    if rnd.kind != RoundKind.AUCTION:
        raise Conflict("WRONG_ROUND_KIND", "Auction lots need an auction round.", kind=rnd.kind)
    if rnd.status not in LIVE_STATUSES:
        raise Conflict(
            "ROUND_NOT_LIVE",
            "Lots can be held only while the round is open or paused.",
            status=rnd.status,
        )
    listing = _fresh_listing(session, listing_id)
    if listing.infinite_supply:
        raise Conflict("INFINITE_SUPPLY", "Auction lots need a finite-supply listing.")
    if session.get(MarketAuctionLot, auction_id) is not None:
        raise Conflict(
            "AUCTION_LOT_EXISTS", "This auction already has a lot.", auction_id=auction_id
        )
    stock = col(MarketListing.stock_remaining)
    taken = session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == listing_id, stock >= quantity)
        .values(stock_remaining=stock - quantity)
        .returning(stock),
    ).one_or_none()
    if taken is None:
        session.refresh(listing)
        raise Conflict("OUT_OF_STOCK", "Not enough stock left.", available=listing.stock_remaining)
    lot = MarketAuctionLot(auction_id=auction_id, listing_id=listing_id, quantity=quantity)
    session.add(lot)
    try:
        session.flush()
    except IntegrityError:
        raise Conflict(
            "AUCTION_LOT_EXISTS", "This auction already has a lot.", auction_id=auction_id
        ) from None
    session.expire(listing)
    return lot


def _lock_lot(session: Session, auction_id: UUID) -> MarketAuctionLot | None:
    return session.exec(
        select(MarketAuctionLot)
        .where(MarketAuctionLot.auction_id == auction_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    ).one_or_none()


def guard_auction_lot(
    session: Session,
    *,
    auction_id: UUID,
    round_id: UUID,
    listing_id: UUID,
    widget_id: UUID,
    quantity: int,
    operation: AuctionOperation,
) -> MarketAuctionLot:
    """Check, inside the caller's transaction, that J may act on an auction.

    Locks the round FOR SHARE (lifecycle cannot change underneath) and the
    lot FOR UPDATE. It never locks the listing row: trades lock ledger
    accounts before the listing, so locking it here could deadlock.
    Bidding (and opening) needs an OPEN round and an unconsumed lot; settling
    and viewing work after the round closes. A paused round blocks bids.
    """
    listing = session.get(MarketListing, listing_id)
    if listing is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    rnd = lock_round(session, listing.round_id)
    if (
        rnd.id != round_id
        or listing.widget_id != widget_id
        or rnd.kind != RoundKind.AUCTION
        or rnd.status == RoundStatus.DRAFT
    ):
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    lot = _lock_lot(session, auction_id)
    if lot is None or lot.listing_id != listing_id or lot.quantity != quantity:
        raise Conflict("AUCTION_LOT_MISSING", "No matching lot is held for this auction.")
    if operation == "bid" and (rnd.status != RoundStatus.OPEN or lot.consumed):
        raise Conflict("ROUND_NOT_OPEN", "Bidding is not open for this round.", status=rnd.status)
    return lot


def consume_auction_lot(session: Session, auction_id: UUID) -> None:
    """J awarded the lot: its units now belong to the winner (via Inventory)."""
    result = session.exec(  # type: ignore[call-overload]
        update(MarketAuctionLot)
        .where(
            col(MarketAuctionLot.auction_id) == auction_id,
            col(MarketAuctionLot.consumed).is_(False),
        )
        .values(consumed=True)
    )
    if result.rowcount != 1:
        raise Conflict("AUCTION_LOT_MISSING", "No unconsumed lot is held for this auction.")


def release_auction_lot(session: Session, auction_id: UUID) -> None:
    """Return an unconsumed lot's units to stock (e.g. an auction with no bids)."""
    lot = _lock_lot(session, auction_id)
    if lot is None or lot.consumed:
        raise Conflict("AUCTION_LOT_MISSING", "No unconsumed lot is held for this auction.")
    stock = col(MarketListing.stock_remaining)
    session.exec(  # type: ignore[call-overload]
        update(MarketListing)
        .where(col(MarketListing.id) == lot.listing_id)
        .values(stock_remaining=stock + lot.quantity)
    )
    session.delete(lot)
    session.flush()
    session.expire_all()
