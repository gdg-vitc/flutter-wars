"""Module I owns registration of the transaction engine."""

from fastapi import FastAPI

from app.contracts.admin import AdminGateway
from app.contracts.market import MarketGateway
from app.contracts.marketplace import Adapters
from app.contracts.trading import TradingGateway
from app.core.auth import require_team
from app.core.config import Settings, get_settings
from app.core.db import session_factory
from app.core.services import gateway, provide
from app.integration.runtime import BackendModules, get_runtime
from app.trading.gateway import TradingGatewayImpl
from app.trading.risk import ResaleRules
from app.trading.router import build_router


def build_runtime(settings: Settings | None = None) -> BackendModules:
    settings = settings or get_settings()
    return BackendModules(
        resale_rules=ResaleRules(
            profit_bps=settings.resale_profit_bps,
            event_profit_bps=settings.resale_event_profit_bps,
            max_loss_bps=settings.resale_max_loss_bps,
        ),
        session_factory=session_factory,
        adapter_factory=Adapters.resolve,
        no_bid_handler=lambda session, auction, adapters: gateway(MarketGateway, session).release_auction_lot(
            auction_id=auction.id
        ),
        freeze_guard=lambda session, scope: gateway(AdminGateway, session).ensure_not_frozen(scope),
    )


def register(app: FastAPI) -> None:
    provide(TradingGateway, TradingGatewayImpl)
    app.include_router(build_router(get_runtime, require_team))
