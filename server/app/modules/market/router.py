from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import Principal, get_principal, require_organizer
from app.core.clock import get_now
from app.core.db import get_session
from app.core.errors import NotFound
from app.modules.market import repository as repo
from app.modules.market import service
from app.modules.market.models import Market, MarketAuctionLot, MarketListing, MarketRound
from app.modules.market.schemas import (
    AuctionLotCreate,
    AuctionLotOut,
    ListingAdminOut,
    ListingCreate,
    ListingOut,
    ListingsOut,
    ListingUpdate,
    MarketCreate,
    MarketOut,
    MarketSummary,
    PriceOut,
    PricingConfigOut,
    RoundAdminOut,
    RoundCreate,
    RoundDetailOut,
    RoundEventOut,
    RoundOut,
    TransitionIn,
)
from app.modules.pricing import service as pricing

router = APIRouter(tags=["market"])
admin = APIRouter(
    prefix="/admin/market", tags=["market admin"], dependencies=[Depends(require_organizer)]
)


def _round_out(rnd: MarketRound) -> RoundOut:
    return RoundOut.model_validate(rnd, from_attributes=True)


def _listings_out(
    session: Session, rnd: MarketRound, now: datetime, *, admin_view: bool
) -> list[ListingOut]:
    listings = service.listings_for_round(session, rnd.id)
    names = repo.widget_names(session, [listing.widget_id for listing in listings])
    quotes = pricing.quote_many(
        session, rnd.id, now
    )  # refreshes `listings` to the quotes' snapshot
    out: list[ListingOut] = []
    for listing in listings:
        q = quotes.get(listing.id)
        fields = dict(
            id=listing.id,
            round_id=listing.round_id,
            widget_id=listing.widget_id,
            widget_name=names.get(listing.widget_id),
            base_price=listing.base_price,
            infinite_supply=listing.infinite_supply,
            supply_total=listing.supply_total,
            stock_remaining=listing.stock_remaining,
            sold_out=listing.stock_remaining == 0,
            max_per_purchase=listing.max_per_purchase,
            price=PriceOut(
                amount=q.price,
                strategy=q.strategy,
                interval_index=q.interval_index,
                valid_until=q.valid_until,
            )
            if q
            else None,
        )
        if admin_view:
            config = pricing.get_config(session, listing.id)
            out.append(
                ListingAdminOut(
                    **fields,
                    pricing=PricingConfigOut(
                        strategy=config.strategy,
                        params=config.params,
                        params_version=config.params_version,
                    ),
                )
            )
        else:
            out.append(ListingOut(**fields))
    return out


def _round_detail(session: Session, rnd: MarketRound, now: datetime) -> RoundDetailOut:
    listings = _listings_out(
        session, rnd, now, admin_view=True
    )  # refreshes `rnd` to the quotes' snapshot
    base = RoundAdminOut.model_validate(rnd, from_attributes=True)
    return RoundDetailOut(**base.model_dump(), listings=listings)


# --- participant ---


@router.get("/market", response_model=MarketSummary)
def market_summary(
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    _: Principal = Depends(get_principal),
) -> MarketSummary:
    market = repo.active_market(session)
    rnd = service.current_round(session)
    return MarketSummary(
        market=MarketOut.model_validate(market, from_attributes=True) if market else None,
        current_round=_round_out(rnd) if rnd else None,
        server_time=now,
    )


@router.get("/market/rounds/current", response_model=RoundOut)
def current_round(
    session: Session = Depends(get_session), _: Principal = Depends(get_principal)
) -> RoundOut:
    rnd = service.current_round(session)
    if rnd is None:
        raise NotFound("NO_CURRENT_ROUND", "No round has started yet.")
    return _round_out(rnd)


@router.get("/market/listings", response_model=ListingsOut)
def current_listings(
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    _: Principal = Depends(get_principal),
) -> ListingsOut:
    rnd = service.current_round(session)
    if rnd is None:
        return ListingsOut(round=None, listings=[], server_time=now)
    listings = _listings_out(
        session, rnd, now, admin_view=False
    )  # refreshes `rnd` to the quotes' snapshot
    return ListingsOut(round=_round_out(rnd), listings=listings, server_time=now)


# --- organizer ---


@admin.post("", response_model=MarketOut, status_code=201)
def create_market(body: MarketCreate, session: Session = Depends(get_session)) -> Market:
    market = service.create_market(session, body.name)
    session.commit()
    session.refresh(market)
    return market


@admin.get("/rounds", response_model=list[RoundAdminOut])
def list_rounds(session: Session = Depends(get_session)) -> list[MarketRound]:
    return repo.rounds(session, service.require_active_market(session).id)


@admin.post("/rounds", response_model=RoundDetailOut, status_code=201)
def create_round(
    body: RoundCreate, session: Session = Depends(get_session), now: datetime = Depends(get_now)
) -> RoundDetailOut:
    rnd = service.create_round(
        session,
        name=body.name,
        kind=body.kind,
        scheduled_open_at=body.scheduled_open_at,
        scheduled_close_at=body.scheduled_close_at,
        listings=[_spec(item) for item in body.listings],
    )
    session.commit()
    return _round_detail(session, rnd, now)


@admin.get("/rounds/{round_id}", response_model=RoundDetailOut)
def get_round(
    round_id: UUID, session: Session = Depends(get_session), now: datetime = Depends(get_now)
) -> RoundDetailOut:
    return _round_detail(session, service.get_round(session, round_id), now)


@admin.get("/rounds/{round_id}/events", response_model=list[RoundEventOut])
def round_events(round_id: UUID, session: Session = Depends(get_session)) -> list:
    return service.round_events(session, round_id)


def _transition_route(action: service.Action):
    def handler(
        round_id: UUID,
        body: TransitionIn | None = None,
        session: Session = Depends(get_session),
        now: datetime = Depends(get_now),
        principal: Principal = Depends(require_organizer),
    ) -> RoundAdminOut:
        body = body or TransitionIn()
        rnd = service.transition(
            session,
            round_id,
            action,
            now=now,
            actor=principal.subject,
            reason=body.reason,
            expected_version=body.expected_version,
        )
        session.commit()
        return RoundAdminOut.model_validate(rnd, from_attributes=True)

    handler.__name__ = f"{action}_round"
    return handler


for _action in ("open", "pause", "close", "finalize"):
    admin.add_api_route(
        f"/rounds/{{round_id}}/{_action}",
        _transition_route(_action),
        methods=["POST"],
        response_model=RoundAdminOut,
        name=f"{_action}_round",
    )


def _spec(item: ListingCreate) -> service.ListingSpec:
    return service.ListingSpec(
        widget_id=item.widget_id,
        base_price=item.base_price,
        supply=item.supply,
        max_per_purchase=item.max_per_purchase,
        pricing_strategy=item.pricing.strategy,
        pricing_params=item.pricing.params,
    )


@admin.post("/rounds/{round_id}/listings", response_model=ListingAdminOut, status_code=201)
def add_listing(
    round_id: UUID,
    body: ListingCreate,
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> ListingAdminOut:
    listing = service.add_listing(session, round_id, _spec(body))
    session.commit()
    return _one_listing(session, listing, now)


@admin.patch("/listings/{listing_id}", response_model=ListingAdminOut)
def update_listing(
    listing_id: UUID,
    body: ListingUpdate,
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> ListingAdminOut:
    changes = {}
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "pricing":
            if value is not None:
                changes["pricing_strategy"] = value.strategy
                changes["pricing_params"] = value.params
        elif field in ("base_price", "supply") and value is None:
            continue  # null means "leave unchanged" for required columns
        else:
            changes[field] = value
    listing = service.update_listing(session, listing_id, **changes)
    session.commit()
    return _one_listing(session, listing, now)


@admin.delete("/listings/{listing_id}", status_code=204)
def delete_listing(listing_id: UUID, session: Session = Depends(get_session)) -> None:
    service.delete_listing(session, listing_id)
    session.commit()


def _one_listing(session: Session, listing: MarketListing, now: datetime) -> ListingAdminOut:
    session.refresh(listing)
    rnd = service.get_round(session, listing.round_id)
    return next(
        item for item in _listings_out(session, rnd, now, admin_view=True) if item.id == listing.id
    )


@admin.post("/listings/{listing_id}/auction-lots", response_model=AuctionLotOut, status_code=201)
def create_auction_lot(
    listing_id: UUID, body: AuctionLotCreate, session: Session = Depends(get_session)
) -> MarketAuctionLot:
    lot = service.create_auction_lot(
        session, listing_id, auction_id=body.auction_id, quantity=body.quantity
    )
    session.commit()
    session.refresh(lot)
    return lot


@admin.delete("/auction-lots/{auction_id}", status_code=204)
def release_auction_lot(auction_id: UUID, session: Session = Depends(get_session)) -> None:
    service.release_auction_lot(session, auction_id)
    session.commit()
