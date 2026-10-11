"""Module G — Market & Round Lifecycle.

Other modules reach it only through MarketGateway (app/contracts/market.py).
"""

from fastapi import FastAPI

from app.contracts.market import MarketGateway
from app.core.services import provide
from app.modules.market import router, stream
from app.modules.market.gateway import MarketGatewayImpl


def register(app: FastAPI) -> None:
    provide(MarketGateway, MarketGatewayImpl)
    app.include_router(stream.router)
    app.include_router(router.router)  # /market
    app.include_router(router.admin)  # /admin/market
