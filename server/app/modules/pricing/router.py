from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.auth import Principal, get_principal, require_organizer
from app.core.clock import get_now
from app.core.db import get_session
from app.modules.market import service as market
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

router = APIRouter(tags=["pricing"])
admin = APIRouter(
    prefix="/admin/market", tags=["pricing admin"], dependencies=[Depends(require_organizer)]
)


@router.get("/market/listings/{listing_id}/price", response_model=PriceQuoteOut)
def listing_price(
    listing_id: UUID,
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
    _: Principal = Depends(get_principal),
) -> PriceQuoteOut:
    market.get_visible_listing(session, listing_id)
    q = service.quote(session, listing_id, now)
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
    session: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> PricingConfigOut:
    state = service.update_pricing(
        session, listing_id, strategy=body.strategy, params=body.params, now=now
    )
    session.commit()
    session.refresh(state)
    return _config_out(state)


@admin.get("/listings/{listing_id}/price-history", response_model=list[PriceHistoryOut])
def history(listing_id: UUID, session: Session = Depends(get_session)) -> list[PriceHistory]:
    market.get_listing(session, listing_id)
    return service.price_history(session, listing_id)
