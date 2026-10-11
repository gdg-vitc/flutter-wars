"""Pricing Engine service (Module H).

Repricing is evaluated lazily: interval k of a round covers
[opened_at + k*interval, opened_at + (k+1)*interval). Whenever a listing's
pricing row is read for a mutation, every interval boundary that has passed
since the stored interval_index is applied in order, using the trades
recorded for that interval. No scheduler or background worker is needed, so
it works the same on a serverless runtime, and the result depends only on
committed inputs.

Recipe for the Transaction Engine (Module I), all inside the caller's
transaction, through the gateways (app/contracts/market.py, pricing.py):

    market.get_for_purchase(...)                          # share-locks the round
    quote = get_current_price(session, listing_id, now)   # locks the pricing row
    market.consume_stock(...)
    record_trade(session, listing_id, qty, TradeSide.BUY, now)
    ... ledger debit at quote.price * qty, inventory increment ...
"""

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlmodel import Session, col, func, select

from app.contracts.market import LISTING_FACTS_COLUMNS, LIVE_STATUSES, ListingFacts, MarketGateway, RoundStatus
from app.contracts.pricing import PriceQuote
from app.core.clock import as_utc
from app.core.errors import AppError, Conflict, NotFound
from app.core.services import gateway
from app.modules.pricing.models import ListingPricing, PriceChangeReason, PriceHistory
from app.modules.pricing.strategies import PricingInput, get_strategy


class TradeSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass(frozen=True)
class _Step:
    interval_index: int
    price: int
    previous_price: int
    demand: int
    supply: int | None
    effective_at: datetime


@dataclass(frozen=True)
class _Advanced:
    price: int
    interval_index: int
    bought: int
    sold: int
    steps: list[_Step]
    valid_until: datetime | None


def _advance(state: ListingPricing, facts: ListingFacts, now: datetime) -> _Advanced:
    """Apply every elapsed interval boundary to `state` without mutating it."""
    strategy = get_strategy(state.strategy)
    params = strategy.params_model.model_validate(state.params)
    seconds = strategy.interval_seconds(params)
    unchanged = _Advanced(
        state.current_price,
        state.interval_index,
        state.interval_bought,
        state.interval_sold,
        [],
        None,
    )
    if seconds is None or facts.opened_at is None:
        return unchanged

    anchor = as_utc(facts.opened_at) + timedelta(seconds=facts.paused_seconds)
    until = as_utc(now)
    if facts.paused_at is not None:
        until = min(until, as_utc(facts.paused_at))
    if facts.closed_at is not None:
        until = min(until, as_utc(facts.closed_at))
    interval = timedelta(seconds=seconds)
    target = max(0, (until - anchor) // interval)

    price, index = state.current_price, state.interval_index
    bought, sold = state.interval_bought, state.interval_sold
    stock = facts.stock_remaining
    # Stock only moves through recorded trades once a round is open, so the
    # supply at the start of the current interval can be reconstructed.
    supply = None if stock is None else stock + bought - sold
    steps: list[_Step] = []
    while index < target:
        new_price = strategy.next_price(
            params,
            PricingInput(facts.base_price, price, bought, sold, supply, facts.supply_total, facts.demand_seed),
        )
        index += 1
        if new_price != price:
            steps.append(_Step(index, new_price, price, bought - sold, supply, anchor + index * interval))
        quiet = bought == 0 and sold == 0
        stable = new_price == price
        price, bought, sold, supply = new_price, 0, 0, stock
        if quiet and stable:
            # Every remaining interval has identical inputs, so nothing else moves.
            index = target
    live = facts.round_status == RoundStatus.OPEN
    valid_until = anchor + (index + 1) * interval if live else None
    return _Advanced(price, index, bought, sold, steps, valid_until)


def _touch(state: ListingPricing, now: datetime) -> None:
    """updated_at is a high-water mark: it never moves backwards, even when a
    trade stamped earlier commits after one stamped later (closing relies on it)."""
    state.updated_at = max(as_utc(state.updated_at), as_utc(now))


def _persist(session: Session, state: ListingPricing, result: _Advanced, now: datetime) -> None:
    if result.interval_index == state.interval_index:
        return
    for step in result.steps:
        session.add(
            PriceHistory(
                listing_id=state.listing_id,
                interval_index=step.interval_index,
                price=step.price,
                previous_price=step.previous_price,
                reason=PriceChangeReason.INTERVAL,
                strategy=state.strategy,
                params_version=state.params_version,
                demand=step.demand,
                supply=step.supply,
                effective_at=step.effective_at,
            )
        )
    state.current_price = result.price
    state.interval_index = result.interval_index
    state.interval_bought = result.bought
    state.interval_sold = result.sold
    _touch(state, now)
    session.add(state)


def _quote(state: ListingPricing, facts: ListingFacts, result: _Advanced) -> PriceQuote:
    return PriceQuote(
        state.listing_id,
        result.price,
        state.strategy,
        result.interval_index,
        result.valid_until,
        facts.stock_remaining,
    )


def _load_for_update(session: Session, listing_id: UUID) -> tuple[ListingPricing, ListingFacts]:
    """Lock round (shared, through Module G) then pricing row (exclusive), the same
    order trades use, and return fresh copies. Stock is read after the pricing lock,
    so it is consistent with the interval counters: every stock change happens under
    this same pricing lock (see the purchase recipe)."""
    market = gateway(MarketGateway, session)
    facts = market.lock_listing_facts(listing_id)
    state = session.exec(
        select(ListingPricing)
        .where(ListingPricing.listing_id == listing_id)
        .with_for_update()
        .execution_options(populate_existing=True),
    ).one_or_none()
    if state is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return state, replace(facts, stock_remaining=market.listing_stock(listing_id))


def _snapshot(
    session: Session, *, listing_id: UUID | None = None, round_id: UUID | None = None
) -> list[tuple[ListingPricing, ListingFacts]]:
    """Pricing state joined to Module G's listing facts in ONE statement, so a quote
    never mixes counters from before a trade with stock from after it."""
    facts = gateway(MarketGateway, session).listing_facts_view()
    statement = (
        select(ListingPricing, *(facts.c[name] for name in LISTING_FACTS_COLUMNS))
        .join(facts, facts.c.listing_id == ListingPricing.listing_id)
        .execution_options(populate_existing=True)
    )
    if listing_id is not None:
        statement = statement.where(facts.c.listing_id == listing_id)
    if round_id is not None:
        statement = statement.where(facts.c.round_id == round_id)
    out = []
    for state, *values in session.exec(statement):
        row = dict(zip(LISTING_FACTS_COLUMNS, values, strict=True))
        out.append((state, ListingFacts(**row | {"round_status": RoundStatus(row["round_status"])})))
    return out


# --- configuration (called by the Market module while a round is a draft) ---


def configure_listing(
    session: Session,
    *,
    listing_id: UUID,
    base_price: int,
    infinite_supply: bool,
    strategy_key: str,
    params: dict[str, Any],
) -> ListingPricing:
    """Create or reset pricing for a listing in a draft round."""
    strategy = get_strategy(strategy_key)
    parsed = strategy.parse_params(params, infinite_supply=infinite_supply)
    state = session.get(ListingPricing, listing_id)
    if state is None:
        state = ListingPricing(listing_id=listing_id, strategy=strategy_key, current_price=base_price)
    else:
        state.params_version += 1
    state.strategy = strategy_key
    state.params = parsed.model_dump(mode="json")
    state.current_price = base_price
    state.interval_index = state.interval_bought = state.interval_sold = 0
    session.add(state)
    return state


def get_config(session: Session, listing_id: UUID) -> ListingPricing:
    state = session.get(ListingPricing, listing_id, populate_existing=True)
    if state is None:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    return state


def get_configs(session: Session, listing_ids: list[UUID]) -> dict[UUID, ListingPricing]:
    """Many listings' pricing configuration in one query."""
    if not listing_ids:
        return {}
    states = session.exec(
        select(ListingPricing)
        .where(col(ListingPricing.listing_id).in_(listing_ids))
        .execution_options(populate_existing=True),
    ).all()
    return {state.listing_id: state for state in states}


def remove_listing(session: Session, listing_id: UUID) -> None:
    state = session.get(ListingPricing, listing_id)
    if state is not None:
        session.delete(state)


def on_round_opened(session: Session, listing_ids: Iterable[UUID], opened_at: datetime) -> None:
    """Record the opening price of every listing when a round first opens."""
    states = session.exec(
        select(ListingPricing)
        .where(col(ListingPricing.listing_id).in_(list(listing_ids)))
        .execution_options(populate_existing=True),
    ).all()
    for state in states:
        session.add(
            PriceHistory(
                listing_id=state.listing_id,
                interval_index=0,
                price=state.current_price,
                reason=PriceChangeReason.INITIAL,
                strategy=state.strategy,
                params_version=state.params_version,
                effective_at=opened_at,
            )
        )


def update_pricing(
    session: Session,
    listing_id: UUID,
    *,
    strategy: str | None,
    params: dict[str, Any],
    now: datetime,
) -> ListingPricing:
    """Organizer edit. Draft rounds: strategy and params may change and the
    price resets to base. Live rounds: params only (see update_params)."""
    state, facts = _load_for_update(session, listing_id)
    if facts.round_status == RoundStatus.DRAFT:
        return configure_listing(
            session,
            listing_id=listing_id,
            base_price=facts.base_price,
            infinite_supply=facts.infinite_supply,
            strategy_key=strategy or state.strategy,
            params=params,
        )
    if strategy is not None and strategy != state.strategy:
        raise Conflict(
            "PRICING_STRATEGY_LOCKED",
            "The pricing strategy cannot change after the round has opened.",
            strategy=state.strategy,
        )
    return update_params(session, listing_id, params, now)


def update_params(session: Session, listing_id: UUID, params: dict[str, Any], now: datetime) -> ListingPricing:
    """Change parameters of a live listing. Elapsed intervals are settled with
    the old parameters first; the new ones apply from the next boundary."""
    state, facts = _load_for_update(session, listing_id)
    if facts.round_status not in LIVE_STATUSES:
        raise Conflict(
            "PRICING_NOT_EDITABLE",
            "Pricing of a closed round cannot change.",
            status=facts.round_status,
        )
    strategy = get_strategy(state.strategy)
    old = strategy.params_model.model_validate(state.params)
    new = strategy.parse_params(params, infinite_supply=facts.infinite_supply)
    if strategy.interval_seconds(new) != strategy.interval_seconds(old):
        raise Conflict(
            "PRICING_INTERVAL_LOCKED",
            "The repricing interval cannot change after the round has opened.",
            interval_seconds=strategy.interval_seconds(old),
        )
    _persist(session, state, _advance(state, facts, now), now)
    state.params = new.model_dump(mode="json")
    state.params_version += 1
    _touch(state, now)
    session.add(state)
    session.add(
        PriceHistory(
            listing_id=listing_id,
            interval_index=state.interval_index,
            price=state.current_price,
            previous_price=state.current_price,
            reason=PriceChangeReason.CONFIG_CHANGE,
            strategy=state.strategy,
            params_version=state.params_version,
            effective_at=now,
        )
    )
    return state


# --- reads ---


def quote(session: Session, listing_id: UUID, now: datetime) -> PriceQuote:
    """Participant-visible price. Read-only: computes, never persists."""
    rows = _snapshot(session, listing_id=listing_id)
    if not rows:
        raise NotFound("LISTING_NOT_FOUND", "Listing not found.", listing_id=listing_id)
    state, facts = rows[0]
    return _quote(state, facts, _advance(state, facts, now))


def quote_many(session: Session, round_id: UUID, now: datetime) -> dict[UUID, PriceQuote]:
    """Quotes for every listing of a round, in one statement. Each quote carries the
    stock it was computed from, so stock shown next to a price is from the same snapshot."""
    return {
        state.listing_id: _quote(state, facts, _advance(state, facts, now))
        for state, facts in _snapshot(session, round_id=round_id)
    }


def latest_activity(session: Session, listing_ids: list[UUID]) -> datetime | None:
    """Latest time any of these listings was traded or repriced."""
    if not listing_ids:
        return None
    latest = session.exec(
        select(func.max(ListingPricing.updated_at)).where(col(ListingPricing.listing_id).in_(listing_ids)),
    ).one()
    return as_utc(latest) if latest else None


def price_history(session: Session, listing_id: UUID) -> list[PriceHistory]:
    return list(
        session.exec(
            select(PriceHistory).where(PriceHistory.listing_id == listing_id).order_by(col(PriceHistory.id)),
        )
    )


# --- internal contract for Transaction (I) ---


def get_current_price(session: Session, listing_id: UUID, now: datetime) -> PriceQuote:
    """Authoritative price for a trade. Locks the listing's pricing row until
    the caller's transaction ends, so concurrent trades on one listing are
    priced one after another against committed state."""
    state, facts = _load_for_update(session, listing_id)
    result = _advance(state, facts, now)
    _persist(session, state, result, now)
    return _quote(state, facts, result)


def recalculate(session: Session, listing_id: UUID, now: datetime) -> PriceQuote:
    """Settle any elapsed intervals now (e.g. from an organizer action)."""
    return get_current_price(session, listing_id, now)


def record_trade(session: Session, listing_id: UUID, quantity: int, side: TradeSide, now: datetime) -> None:
    """Count a committed-in-this-transaction trade towards the current
    interval's demand. Call in the same transaction as the stock change."""
    if quantity < 1:
        raise AppError("INVALID_QUANTITY", "Quantity must be at least 1.", quantity=quantity)
    state, facts = _load_for_update(session, listing_id)
    _persist(session, state, _advance(state, facts, now), now)
    if side == TradeSide.BUY:
        state.interval_bought += quantity
    else:
        state.interval_sold += quantity
    _touch(state, now)
    session.add(state)
