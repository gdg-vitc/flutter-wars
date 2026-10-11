from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.contracts.admin import OrganizerPrincipal, Permission
from app.contracts.market import MarketGateway
from app.core.audit import audit
from app.core.auth import Principal, get_principal, require_permission
from app.core.clock import get_now
from app.core.db import get_session
from app.core.read_cache import market_reads
from app.core.services import gateway
from app.modules.pricing import service
from app.modules.pricing.models import ListingPricing, PriceHistory
from app.modules.pricing.schemas import (
    PriceHistoryOut,
    PriceQuoteOut,
    PricingConfigOut,
    PricingUpdate,
    StrategyOut,
)
from app.modules.pricing.strategies import available_strategies, get_strategy

# Module K decides who is an organizer and what they may do.
MARKET_MANAGE = require_permission(Permission.MARKET_MANAGE)

router = APIRouter(tags=["pricing"])
admin = APIRouter(prefix="/admin/market", tags=["pricing admin"], dependencies=[Depends(MARKET_MANAGE)])


@router.get("/market/listings/{listing_id}/price", response_model=PriceQuoteOut)
def listing_price(
    listing_id: UUID,
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    _: Principal = Depends(get_principal),
) -> PriceQuoteOut:
    def load():
        gateway(MarketGateway, session).require_listing(listing_id, visible_only=True)
        return service.quote(session, listing_id, now)

    q = market_reads.read(
        session, ("listing-price", listing_id), now, load, deadlines=lambda quote: (quote.valid_until,)
    )
    return PriceQuoteOut(
        listing_id=q.listing_id,
        price=q.price,
        strategy=q.strategy,
        interval_index=q.interval_index,
        valid_until=q.valid_until,
        server_time=now,
    )


@admin.get("/pricing-strategies", response_model=list[StrategyOut])
def strategies() -> list[StrategyOut]:
    return [
        StrategyOut(key=key, params_schema=get_strategy(key).params_model.model_json_schema())
        for key in available_strategies()
    ]


def _config_out(state: ListingPricing) -> PricingConfigOut:
    return PricingConfigOut(
        listing_id=state.listing_id,
        strategy=state.strategy,
        params=state.params,
        params_version=state.params_version,
        current_price=state.current_price,
        interval_index=state.interval_index,
    )


@admin.get("/listings/{listing_id}/pricing", response_model=PricingConfigOut)
def get_pricing(listing_id: UUID, session: Session = Depends(get_session)) -> PricingConfigOut:
    return _config_out(service.get_config(session, listing_id))


@admin.patch("/listings/{listing_id}/pricing", response_model=PricingConfigOut)
def update_pricing(
    listing_id: UUID,
    body: PricingUpdate,
    organizer: OrganizerPrincipal = Depends(MARKET_MANAGE),
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> PricingConfigOut:
    state = service.update_pricing(session, listing_id, strategy=body.strategy, params=body.params, now=now)
    audit(
        session,
        organizer,
        "pricing.update",
        target_type="listing",
        target_id=listing_id,
        reason="Organizer updated pricing",
        details={"strategy": state.strategy, "params_version": state.params_version},
    )
    session.commit()
    session.refresh(state)
    return _config_out(state)


@admin.get("/listings/{listing_id}/price-history", response_model=list[PriceHistoryOut])
def history(listing_id: UUID, session: Session = Depends(get_session)) -> list[PriceHistory]:
    gateway(MarketGateway, session).require_listing(listing_id, visible_only=False)
    return service.price_history(session, listing_id)
