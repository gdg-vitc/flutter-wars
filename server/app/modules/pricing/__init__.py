"""Module H publishes its pricing gateway and owns its HTTP routes."""

from fastapi import FastAPI

from app.contracts.pricing import PricingGateway
from app.core.services import provide
from app.modules.pricing import router
from app.modules.pricing.gateway import PricingGatewayImpl


def register(app: FastAPI) -> None:
    provide(PricingGateway, PricingGatewayImpl)
    app.include_router(router.router)
    app.include_router(router.admin)
