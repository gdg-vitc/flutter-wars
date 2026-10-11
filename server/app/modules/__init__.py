"""Foundation discovers modules; each module registers its own gateway and routes."""

from fastapi import FastAPI

from app import auction, trading
from app.modules import admin, authentication, catalog, foundation, ide_sync, inventory, ledger, market, pricing


def register_modules(app: FastAPI) -> None:
    for module in (
        foundation,
        authentication,
        ide_sync,
        catalog,
        ledger,
        inventory,
        market,
        pricing,
        admin,
        trading,
        auction,
    ):
        module.register(app)
