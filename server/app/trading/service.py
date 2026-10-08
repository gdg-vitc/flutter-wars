from uuid import UUID

from app.integration.contracts import (
    MAX_CREDITS,
    BrokeragePolicy,
    CatalogPort,
    InventoryPort,
    LedgerPort,
    MarketPort,
    PricingPort,
)
from app.integration.errors import ConfigurationRequired, NotFound
from app.integration.transactions import lock_request, require_transaction

from .errors import AmountTooLarge, IdempotencyConflict, InvalidPrice
from .models import TradeTransaction, TradeType
from .repository import TradeRepository
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
    ):
        self._trades = trades
        self._market = market
        self._pricing = pricing
        self._ledger = ledger
        self._inventory = inventory
        self._catalog = catalog
        self._brokerage = brokerage

    def _get_existing(
        self, *, team_id: UUID, request: PurchaseRequest, kind: TradeType
    ) -> TradeTransaction | None:
        existing = self._trades.get_by_idempotency_key(
            team_id=team_id, transaction_type=kind, idempotency_key=request.idempotency_key
        )
        if existing and (
            existing.listing_id != request.listing_id or existing.quantity != request.quantity
        ):
            raise IdempotencyConflict()
        return existing

    def _get_existing_purchase(
        self, *, team_id: UUID, request: PurchaseRequest
    ) -> TradeTransaction | None:
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

    def _trade(
        self, *, team_id: UUID, request: PurchaseRequest, kind: TradeType
    ) -> TradeTransaction:
        require_transaction(self._trades.session)
        request = PurchaseRequest.model_validate(request.model_dump())
        lock_request(
            self._trades.session, scope=f"trade:{team_id}:{kind.value}:{request.idempotency_key}"
        )
        existing = self._get_existing(team_id=team_id, request=request, kind=kind)
        if existing:
            return existing
        if kind == TradeType.SELL and self._brokerage is None:
            raise ConfigurationRequired()
        if kind == TradeType.SELL:
            # Serialize this team's resales into one listing, so the prior quantity
            # the brokerage sees cannot be raced by a concurrent split sale.
            lock_request(self._trades.session, scope=f"resale:{team_id}:{request.listing_id}")
        get_listing = (
            self._market.get_for_purchase if kind == TradeType.BUY else self._market.get_for_resale
        )
        listing = get_listing(listing_id=request.listing_id, quantity=request.quantity)
        if listing.listing_id != request.listing_id:
            raise InvalidPrice("Adapter returned mismatched listing identity.")
        self._catalog.validate_widget(
            widget_id=listing.widget_id, operation="buy" if kind == TradeType.BUY else "sell"
        )
        price, gross = self._get_purchase_amounts(request=request)
        fee = 0
        if kind == TradeType.SELL:
            fee = self._brokerage.fee(
                team_id=team_id,
                listing=listing,
                quantity=request.quantity,
                unit_price=price,
                gross_amount=gross,
                prior_quantity=self._trades.sold_quantity(
                    team_id=team_id, listing_id=listing.listing_id
                ),
            )
            if type(fee) is not int or not 0 <= fee <= gross:
                raise ConfigurationRequired("Brokerage must return a valid whole-credit fee.")
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
        self._ledger.lock_accounts(team_ids=[team_id])
        if kind == TradeType.BUY:
            self._ledger.debit(team_id=team_id, amount=trade.final_amount, trade_id=trade.id)
            self._market.consume_stock(
                listing_id=listing.listing_id, quantity=request.quantity, trade_id=trade.id
            )
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
            self._market.restore_stock(
                listing_id=listing.listing_id, quantity=request.quantity, trade_id=trade.id
            )
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
