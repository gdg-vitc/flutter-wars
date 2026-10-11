"""Module J owns registration of auction HTTP routes."""

from fastapi import FastAPI

from app.auction.gateway import AuctionGatewayImpl
from app.auction.router import build_router
from app.contracts.admin import Permission
from app.contracts.auction import AuctionGateway
from app.core.auth import require_permission, require_team
from app.core.services import provide
from app.integration.runtime import get_runtime


def register(app: FastAPI) -> None:
    provide(AuctionGateway, AuctionGatewayImpl)
    app.include_router(build_router(get_runtime, require_team, require_permission(Permission.MARKET_MANAGE)))
