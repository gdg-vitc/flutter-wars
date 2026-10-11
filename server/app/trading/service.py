from uuid import UUID

from app.contracts.marketplace import (
    MAX_CREDITS,
    BrokeragePolicy,
    CatalogPort,
    InventoryPort,
    LedgerPort,
    MarketPort,
    PricingPort,
)
from app.contracts.marketplace_errors import ConfigurationRequired, NotFound
from app.core.errors import AppError
from app.core.transactions import lock_request, require_transaction

from .errors import AmountTooLarge, IdempotencyConflict, InvalidPrice
from .models import TradeTransaction, TradeType
from .repository import TradeRepository
from .risk import ResaleRules
from .schemas import PurchaseRequest, SellRequest


class TradingService:
    """Business orchestration only. Runtime owns begin/commit/rollback."""

    def __init__(
        self,
        *,
        trades: TradeRepository,
        market: MarketPort,
        pricing: PricingPort,
        ledger: LedgerPort,
        inventory: InventoryPort,
        catalog: CatalogPort,
        brokerage: BrokeragePolicy | None = None,
        resale_rules: ResaleRules | None = None,
    ):
        self._trades = trades
        self._market = market
        self._pricing = pricing
        self._ledger = ledger
        self._inventory = inventory
        self._catalog = catalog
        self._brokerage = brokerage
        self._resale_rules = resale_rules

    def _get_existing(self, *, team_id: UUID, request: PurchaseRequest, kind: TradeType) -> TradeTransaction | None:
        existing = self._trades.get_by_idempotency_key(
            team_id=team_id, transaction_type=kind, idempotency_key=request.idempotency_key
        )
        if existing and (existing.listing_id != request.listing_id or existing.quantity != request.quantity):
            raise IdempotencyConflict()
        return existing

    def _get_existing_purchase(self, *, team_id: UUID, request: PurchaseRequest) -> TradeTransaction | None:
        return self._get_existing(team_id=team_id, request=request, kind=TradeType.BUY)

    def _get_purchase_amounts(self, *, request: PurchaseRequest) -> tuple[int, int]:
        unit_price = self._pricing.get_unit_price(listing_id=request.listing_id)
        if type(unit_price) is not int or unit_price < 0:
            raise InvalidPrice()
        gross = unit_price * request.quantity
        if gross > MAX_CREDITS:
            raise AmountTooLarge()
        return unit_price, gross

    def purchase(self, *, team_id: UUID, request: PurchaseRequest) -> TradeTransaction:
        return self._trade(team_id=team_id, request=request, kind=TradeType.BUY)

    def sell(self, *, team_id: UUID, request: SellRequest) -> TradeTransaction:
        return self._trade(team_id=team_id, request=request, kind=TradeType.SELL)

    def resale_quote(self, *, team_id: UUID, request: SellRequest) -> dict:
        require_transaction(self._trades.session)
        if self._resale_rules is None:
            raise ConfigurationRequired()
        lock_request(self._trades.session, scope=f"resale-account:{team_id}")
        listing = self._market.get_for_resale(listing_id=request.listing_id, quantity=request.quantity)
        self._catalog.validate_widget(widget_id=listing.widget_id, operation="sell")
        price, gross = self._get_purchase_amounts(request=request)
        self._ledger.lock_accounts(team_ids=[team_id])
        owned = self._inventory.owned_quantity(team_id, listing.widget_id)
        if request.quantity > owned:
            from app.contracts.marketplace_errors import InsufficientInventory

            raise InsufficientInventory()
        account, position = self._trades.resale_state(team_id, listing.widget_id)
        self._resale_rules.synchronize(position, owned)
        return self._resale_rules.quote(
            position,
            account,
            quantity=request.quantity,
            gross=gross,
            external=self._trades.external_units(team_id, listing.widget_id),
            funding=self._ledger.initial_funding(team_id),
            gift_unit_price=self._market.lock_listing_facts(request.listing_id).base_price
            if request.quantity > position.quantity
            else 0,
        ) | {"unit_price": price}

    def _trade(self, *, team_id: UUID, request: PurchaseRequest, kind: TradeType) -> TradeTransaction:
        require_transaction(self._trades.session)
        request = (SellRequest if kind == TradeType.SELL else PurchaseRequest).model_validate(request.model_dump())
        lock_request(self._trades.session, scope=f"trade:{team_id}:{kind.value}:{request.idempotency_key}")
        if self._resale_rules is not None:
            lock_request(self._trades.session, scope=f"resale-account:{team_id}")
        existing = self._get_existing(team_id=team_id, request=request, kind=kind)
        if existing:
            return existing
        if kind == TradeType.SELL and self._brokerage is None:
            raise ConfigurationRequired()
        if kind == TradeType.SELL:
            # Serialize this team's resales into one listing, so the prior quantity
            # the brokerage sees cannot be raced by a concurrent split sale.
            lock_request(self._trades.session, scope=f"resale:{team_id}:{request.listing_id}")
        get_listing = self._market.get_for_purchase if kind == TradeType.BUY else self._market.get_for_resale
        listing = get_listing(listing_id=request.listing_id, quantity=request.quantity)
        if listing.listing_id != request.listing_id:
            raise InvalidPrice("Adapter returned mismatched listing identity.")
        self._catalog.validate_widget(widget_id=listing.widget_id, operation="buy" if kind == TradeType.BUY else "sell")
        price, gross = self._get_purchase_amounts(request=request)
        fee = 0
        if kind == TradeType.SELL and self._resale_rules is None:
            fee = self._brokerage.fee(
                team_id=team_id,
                listing=listing,
                quantity=request.quantity,
                unit_price=price,
                gross_amount=gross,
                prior_quantity=self._trades.sold_quantity(team_id=team_id, listing_id=listing.listing_id),
            )
            if type(fee) is not int or not 0 <= fee <= gross:
                raise ConfigurationRequired("Brokerage must return a valid whole-credit fee.")
        self._ledger.lock_accounts(team_ids=[team_id])
        if self._resale_rules is not None:
            if kind == TradeType.BUY and request.max_unit_price is not None and price > request.max_unit_price:
                raise AppError("PRICE_LIMIT_EXCEEDED", "The price increased; refresh your quote.", 409)
            account, position = self._trades.resale_state(team_id, listing.widget_id)
            self._resale_rules.synchronize(position, self._inventory.owned_quantity(team_id, listing.widget_id))
            external = self._trades.external_units(team_id, listing.widget_id)
            if kind == TradeType.SELL:
                settlement = self._resale_rules.quote(
                    position,
                    account,
                    quantity=request.quantity,
                    gross=gross,
                    external=external,
                    funding=self._ledger.initial_funding(team_id),
                    gift_unit_price=self._market.lock_listing_facts(request.listing_id).base_price
                    if request.quantity > position.quantity
                    else 0,
                )
                fee = gross - settlement["final_amount"]
                if request.min_final_amount is not None and settlement["final_amount"] < request.min_final_amount:
                    raise AppError("PRICE_LIMIT_EXCEEDED", "Resale proceeds decreased; refresh your quote.", 409)
                self._resale_rules.consume(position, account, settlement)
            else:
                if position.quantity == 0:
                    position.reward_units = 0
                position.reward_units += request.quantity
                position.quantity += request.quantity
                position.cost += gross
                position.external_units += external * request.quantity
        trade = TradeTransaction.model_validate(
            dict(
                team_id=team_id,
                listing_id=listing.listing_id,
                widget_id=listing.widget_id,
                transaction_type=kind,
                quantity=request.quantity,
                unit_price=price,
                gross_amount=gross,
                brokerage_amount=fee,
                final_amount=gross - fee,
                idempotency_key=request.idempotency_key,
            )
        )
        if kind == TradeType.BUY:
            self._ledger.debit(team_id=team_id, amount=trade.final_amount, trade_id=trade.id)
            self._market.consume_stock(listing_id=listing.listing_id, quantity=request.quantity, trade_id=trade.id)
            self._inventory.add(
                team_id=team_id,
                widget_id=listing.widget_id,
                quantity=request.quantity,
                reference=trade.id,
            )
        else:
            self._inventory.remove(
                team_id=team_id,
                widget_id=listing.widget_id,
                quantity=request.quantity,
                reference=trade.id,
            )
            self._market.restore_stock(listing_id=listing.listing_id, quantity=request.quantity, trade_id=trade.id)
            self._ledger.credit(team_id=team_id, amount=trade.final_amount, trade_id=trade.id)
        self._trades.add(trade)
        self._pricing.record_trade(
            listing_id=listing.listing_id,
            transaction_type=kind.value,
            quantity=request.quantity,
            unit_price=price,
            trade_id=trade.id,
        )
        return trade

    def history(self, *, team_id: UUID, limit: int = 50, offset: int = 0) -> list[TradeTransaction]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("Invalid pagination.")
        return self._trades.history(team_id=team_id, limit=limit, offset=offset)

    def detail(self, *, team_id: UUID, trade_id: UUID) -> TradeTransaction:
        trade = self._trades.get_owned(team_id=team_id, trade_id=trade_id)
        if trade is None:
            raise NotFound()
        return trade
